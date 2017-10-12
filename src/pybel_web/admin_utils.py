# -*- coding: utf-8 -*-

from flask import redirect, request
from flask_admin.contrib.sqla import ModelView as ModelViewBase
from flask_security import current_user, url_for_security


class ModelView(ModelViewBase):
    """Adds plugin for Flask-Security to Flask-Admin model views"""

    def is_accessible(self):
        """Checks the current user is an admin"""
        return current_user.is_authenticated and current_user.is_admin

    def inaccessible_callback(self, name, **kwargs):
        """redirect to login page if user doesn't have access"""
        return redirect(url_for_security('login', next=request.url))


class NetworkView(ModelView):
    """Special view for PyBEL Networks"""
    column_exclude_list = ['blob', 'sha512', 'authors', 'description', 'copyright', 'disclaimer', 'licenses']


class ReportView(ModelView):
    """Special view for reports"""
    column_exclude_list = ['source', 'calculations']


class NodeView(ModelView):
    """Special view for PyBEL Nodes"""
    column_exclude_list = ['blob', 'sha512']


class EdgeView(ModelView):
    """Special view for PyBEL Edges"""
    column_exclude_list = ['blob', 'sha512']


class CitationView(ModelView):
    column_exclude_list = ['blob', 'sha512']


class EvidenceView(ModelView):
    column_exclude_list = ['blob', 'sha512']


class ExperimentView(ModelView):
    column_exclude_list = ['source', 'result', ]


class UserView(ModelView):
    """Special view for PyBEL Web Users"""
    column_exclude_list = ['password', ]
