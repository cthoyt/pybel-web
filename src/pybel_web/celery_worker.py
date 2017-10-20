# -*- coding: utf-8 -*-

"""
Run the celery worker with:

:code:`python3 -m celery -A pybel_web.celery_worker.celery worker`

While also laughing at how ridiculously redundant this nomenclature is.
"""

import hashlib
import logging
import os
import time
import uuid

import requests.exceptions
from celery.utils.log import get_task_logger
from flask_mail import Message
from six.moves.cPickle import dumps, loads
from sqlalchemy.exc import IntegrityError, OperationalError

from pybel import from_url, to_bel_lines, to_bel_path, to_bytes
from pybel.constants import METADATA_CONTACT, METADATA_DESCRIPTION, METADATA_LICENSES, PYBEL_DATA_DIR
from pybel.manager.models import Network
from pybel.parser.parse_exceptions import InconsistentDefinitionError
from pybel.struct import union
from pybel_tools.constants import BMS_BASE
from pybel_tools.ioutils import convert_directory
from pybel_tools.mutation import add_canonical_names, add_identifiers, enrich_pubmed_citations, infer_central_dogma
from pybel_tools.utils import enable_cool_mode
from pybel_web.application import create_application
from pybel_web.celery_utils import create_celery
from pybel_web.constants import CHARLIE_EMAIL, DANIEL_EMAIL, integrity_message, log_worker_path
from pybel_web.models import Experiment, Project, Report, User
from pybel_web.utils import calculate_scores, fill_out_report, make_graph_summary, manager

log = get_task_logger(__name__)

fh = logging.FileHandler(log_worker_path)
fh.setLevel(logging.DEBUG)
log.addHandler(fh)

logging.basicConfig(level=logging.DEBUG)
enable_cool_mode()  # turn off warnings for compilation
log.setLevel(logging.DEBUG)

app = create_application()
celery = create_celery(app)

dumb_belief_stuff = {
    METADATA_DESCRIPTION: {'Document description'},
    METADATA_CONTACT: {'your@email.com'},
    METADATA_LICENSES: {'Document license'}
}

pbw_sender = ("PyBEL Web", 'pybel@scai.fraunhofer.de')


def parse_folder(folder, **kwargs):
    """Parses everything in a folder

    :param str folder:
    """
    convert_directory(
        folder,
        connection=manager,
        upload=True,
        canonicalize=True,
        infer_central_dogma=True,
        enrich_citations=True,
        enrich_genes=True,
        enrich_go=False,
        **kwargs
    )


@celery.task(name='parse-aetionomy')
def parse_aetionomy():
    """Converts the AETIONOMY folder in the BMS"""
    folder = os.path.join(os.environ[BMS_BASE], 'aetionomy')
    parse_folder(folder)


@celery.task(name='parse-selventa')
def parse_selventa():
    """Converts the Selventa folder in the BMS"""
    folder = os.path.join(os.environ[BMS_BASE], 'selventa')
    parse_folder(folder, citation_clearing=False, allow_nested=True)


@celery.task(name='parse-bel4imocede')
def parse_bel4imocede():
    """Converts the BEL4IMOCEDE folder in the BMS"""
    folder = os.path.join(os.environ[BMS_BASE], 'BEL4IMOCEDE')
    parse_folder(folder)


@celery.task(name='parse-ptsd')
def parse_ptsd():
    """Converts the CVBIO PTSD folder in the BMS"""
    folder = os.path.join(os.environ[BMS_BASE], 'cvbio', 'PTSD')
    parse_folder(folder)


@celery.task(name='parse-tbi')
def parse_tbi():
    """Converts the CVBIO TBI folder in the BMS"""
    folder = os.path.join(os.environ[BMS_BASE], 'cvbio', 'TBI')
    parse_folder(folder)


@celery.task(name='parse-bms')
def parse_bms():
    """Converts the entire BMS"""
    parse_folder(os.environ[BMS_BASE])


@celery.task(name='parse-url')
def parse_by_url(url):
    """Parses a graph at the given URL resource"""
    # FIXME add proper exception handling and feedback
    try:
        graph = from_url(url, manager=manager)
    except:
        return 'Parsing failed for {}. '.format(url)

    try:
        network = manager.insert_graph(graph)
        return network.id
    except:
        manager.session.rollback()
        return 'Inserting failed for {}'.format(url)
    finally:
        manager.session.close()


@celery.task(name='pybelparser')
def async_parser(report_id):
    """Asynchronously parses a BEL script and sends email feedback

    :param int report_id: Report identifier
    """
    t = time.time()

    report = manager.session.query(Report).get(report_id)

    if report is None:
        raise ValueError('Report {} not found'.format(report_id))

    report_id = report.id
    source_name = report.source_name

    log.info('Starting parse task for %s (report %s)', source_name, report_id)

    def make_mail(subject, body):
        if 'mail' not in app.extensions:
            return

        with app.app_context():
            app.extensions['mail'].send_message(
                subject=subject,
                recipients=[report.user.email],
                body=body,
                sender=pbw_sender,
            )

    def finish_parsing(subject, body, log_exception=True):
        if log_exception:
            log.exception(body)
        make_mail(subject, body)
        report.message = body
        manager.session.commit()
        return body

    try:
        log.info('parsing graph')
        graph = report.parse_graph(manager=manager)

    except requests.exceptions.ConnectionError:
        message = 'Connection to resource could not be established.'
        return finish_parsing('Parsing Failed for {}'.format(source_name), message)

    except InconsistentDefinitionError as e:
        message = 'Parsing failed for {} because {} was redefined on line {}.'.format(source_name, e.definition,
                                                                                      e.line_number)
        return finish_parsing('Parsing Failed for {}'.format(source_name), message)

    except Exception as e:
        message = 'Parsing failed for {} from a general error: {}'.format(source_name, e)
        return finish_parsing('Parsing Failed for {}'.format(source_name), message)

    if not graph.name:
        message = 'Parsing failed for {} because SET DOCUMENT Name was missing.'.format(source_name)
        return finish_parsing('Parsing Failed for {}'.format(source_name), message)

    if not graph.version:
        message = 'Parsing failed for {} because SET DOCUMENT Version was missing.'.format(source_name)
        return finish_parsing('Parsing Failed for {}'.format(source_name), message)

    problem = {
        k: v
        for k, v in graph.document.items()
        if k in dumb_belief_stuff and v in dumb_belief_stuff[k]
    }

    if problem:
        message = '{} was rejected because it has "default" metadata: {}'.format(source_name, problem)
        return finish_parsing('Rejected {}'.format(source_name), message)

    network = manager.session.query(Network).filter(Network.name == graph.name,
                                                    Network.version == graph.version).one_or_none()

    if network is not None:
        message = integrity_message.format(graph.name, graph.version)

        if network.report.user == report.user:  # This user is being a fool
            return finish_parsing('Uploading Failed for {}'.format(source_name), message)

        if hashlib.sha1(network.blob).hexdigest() != hashlib.sha1(to_bytes(network)).hexdigest():
            with app.app_context():
                app.extensions['mail'].send_message(
                    subject='Possible attempted Espionage',
                    recipients=[CHARLIE_EMAIL, DANIEL_EMAIL],
                    body='The following user ({} {}) may have attempted espionage of network: {}'.format(
                        report.user.id,
                        report.user.email,
                        network
                    ),
                    sender=pbw_sender,
                )

            return finish_parsing('Upload Failed for {}'.format(source_name), message)

        # Grant rights to this user
        network.users.append(report.user)
        manager.session.commit()

        message = 'Granted rights for {} to {} after parsing {}'.format(network, report.user, source_name)
        return finish_parsing('Granted Rights from {}'.format(source_name), message, log_exception=False)

    try:
        log.info('enriching graph')
        add_canonical_names(graph)

        add_identifiers(graph)

        if report.infer_origin:
            infer_central_dogma(graph)

        enrich_pubmed_citations(graph, manager=manager)

    except (IntegrityError, OperationalError):
        manager.session.rollback()
        log.exception('problem with database while fixing citations')

    except:
        log.exception('problem fixing citations')

    upload_failed_text = 'Upload Failed for {}'.format(source_name)

    try:
        log.info('inserting graph')
        network = manager.insert_graph(graph, store_parts=app.config.get('PYBEL_USE_EDGE_STORE', True))

    except IntegrityError:
        manager.session.rollback()
        message = integrity_message.format(graph.name, graph.version)
        return finish_parsing(upload_failed_text, message)

    except OperationalError:
        manager.session.rollback()
        message = 'Database is locked. Unable to upload.'
        return finish_parsing(upload_failed_text, message)

    except Exception as e:
        manager.session.rollback()
        message = "Error storing in database: {}".format(e)
        return finish_parsing(upload_failed_text, message)

    log.info('done storing [%d]. starting to make report.', network.id)

    graph_summary = make_graph_summary(graph)

    try:
        fill_out_report(network, report, graph_summary)
        report.time = time.time() - t

        manager.session.add(report)
        manager.session.commit()

        log.info('report #%d complete [%d]', report.id, network.id)
        make_mail('Successfully uploaded {} ({})'.format(source_name, graph),
                  '{} ({}) is done parsing. Check the network list page.'.format(source_name, graph))

        return network.id

    except Exception as e:
        manager.session.rollback()
        make_mail('Report unsuccessful for {}'.format(source_name), str(e))
        log.exception('Problem filling out report')
        return -1

    finally:
        manager.session.close()


@celery.task(name='merge-project')
def merge_project(user_id, project_id):
    """Merges the graphs in a project and does stuff

    :param int user_id: The database identifier of the user
    :param int project_id: The database identifier of the project
    """
    t = time.time()

    user = manager.session.query(User).get(user_id)
    project = manager.session.query(Project).get(project_id)

    graphs = [network.as_bel() for network in project.networks]

    graph = union(graphs)

    # option 1 - store back into database
    # rg = manager.insert_graph(graph)

    # option 2 - store as temporary file, then serve that shit
    # need to get secure file path and secure directory
    graph.name = uuid.uuid4()
    graph.version = '1.0.0'

    if 'mail' not in app.extensions:
        path = os.path.join(PYBEL_DATA_DIR, '{}.bel'.format(graph.name))
        to_bel_path(graph, path)
        log.warning('Merge took %.2f seconds to %s', time.time() - t, path)
        return

    lines = to_bel_lines(graph)
    s = '\n'.join(lines)

    log.info('Merge took %.2f seconds', time.time() - t)

    msg = Message(
        subject='Merged {} BEL'.format(project.name),
        recipients=[user.email],
        body='The BEL documents from {} were merged. The resulting BEL script is attached'.format(project.name),
        sender=pbw_sender
    )

    msg.attach('merged.bel', 'text/plain', s)

    app.extensions['mail'].send(msg)

    return 1


@celery.task(name='run-cmpa')
def run_cmpa(experiment_id):
    """Runs the CMPA analysis

    :param int experiment_id:
    """
    log.info('Running experiment %s', experiment_id)

    experiment = manager.session.query(Experiment).get(experiment_id)

    graph = experiment.query.run(manager)

    df = loads(experiment.source)

    gene_column = experiment.gene_column
    data_column = experiment.data_column

    data = {
        k: v
        for _, k, v in df.loc[df[gene_column].notnull(), [gene_column, data_column]].itertuples()
    }

    scores = calculate_scores(graph, data, experiment.permutations)

    experiment.result = dumps(scores)
    experiment.completed = True

    try:
        manager.session.commit()
    except:
        manager.session.rollback()
        return -1
    finally:
        manager.session.close()

    message = 'Experiment {} on query {} with {} has completed'.format(
        experiment_id,
        experiment.query_id,
        experiment.source_name
    )

    if 'mail' in app.extensions:
        with app.app_context():
            app.extensions['mail'].send_message(
                subject='CMPA Analysis complete',
                recipients=[experiment.user.email],
                body=message,
                sender=pbw_sender,
            )

    return experiment_id
