# -*- coding: utf-8 -*-
#
import urllib

from django.test import Client, TestCase
from django.core.urlresolvers import reverse
from django.utils import simplejson
from django.contrib.auth.models import User

import settings
from manabi.test_helpers import ManabiTestCase, create_sample_data, create_user


class StatsTest(ManabiTestCase):
    #fixtures = ['sample_db.json']
    def setUp(self):
        user = create_user()
        create_sample_data(facts=30, user=user)
        self.review_cards(user)

    def test_reps_view(self):
        res = self.get(reverse('graphs_repetitions'))
        self.assertStatus(200, res)

        self.assertApiSuccess(res)
        json = simplejson.loads(res.content)
        self.assertTrue('series' in json['data'])
        
    def test_due_counts_view(self):
        res = self.get(reverse('graphs_due_counts'))
        self.assertStatus(200, res)

        self.assertApiSuccess(res)
        json = simplejson.loads(res.content)
        self.assertTrue('series' in json['data'])

