# -*- coding: utf-8 -*-

"""Run BEL Commons as a WSGI application.

Run with GUnicorn: ``gunicorn -w 4 -b 0.0.0.0:5000 pybel_web.run:app``
"""

import logging

from pybel_web.analysis_service import experiment_blueprint
from pybel_web.application import create_application
from pybel_web.curation_service import curation_blueprint
from pybel_web.database_service import api_blueprint
from pybel_web.external_services import external_blueprint
from pybel_web.main_service import ui_blueprint
from pybel_web.parser_endpoint import build_parser_service
from pybel_web.parser_service import parser_blueprint
from pybel_web.views import receiving_blueprint, reporting_blueprint

datefmt = '%H:%M:%S'
fmt = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"

level = logging.INFO
logging.basicConfig(level=level, format=fmt, datefmt=datefmt)

pybel_log = logging.getLogger('pybel')
pybel_log.setLevel(level)

pbt_log = logging.getLogger('pybel_tools')
pbt_log.setLevel(level)

pbw_log = logging.getLogger('pybel_web')
pbw_log.setLevel(level)

app = create_application()

app.register_blueprint(ui_blueprint)
app.register_blueprint(curation_blueprint)
app.register_blueprint(parser_blueprint)
app.register_blueprint(api_blueprint)
app.register_blueprint(experiment_blueprint)
app.register_blueprint(external_blueprint)
app.register_blueprint(reporting_blueprint)
app.register_blueprint(receiving_blueprint)


if app.config.get('PYBEL_WEB_PARSER_API'):
    build_parser_service(app)

pbw_log.info('done creating app')
