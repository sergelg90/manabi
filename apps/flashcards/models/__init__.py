from cards import * #Card, CardHistory, CardStatistics
from facts import * #Fact, FactType
from fields import *
from decks import *
from cardtemplates import *

from django.db.models.signals import post_save  

from django.conf import settings

#preloading data
#TODO move into fixtures ?
def create_default_user_data(sender, instance, created, **kwargs):
    """
    Creates the default fact types, fields, deck and card templates.
    Meant to be used when a new user signs up.
    """
    if created:
      user = instance
      
      my_deck = Deck(name='My First Deck', owner=user)
      my_deck.save()

      my_deck_scheduling = SchedulingOptions(deck=my_deck)
      my_deck_scheduling.save()
      
post_save.connect(create_default_user_data, sender=User) 


