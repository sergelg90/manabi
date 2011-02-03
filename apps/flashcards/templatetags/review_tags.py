from django import template
from flashcards.models import CardHistory, Deck
from ..contextprocessors import review_start_context
from copy import copy

register = template.Library()


def _show_review_start_buttons(context, deck=None):
    context = copy(context)
    context['deck'] = deck
    return context.update(review_start_context(context['request']))

@register.inclusion_tag(
    'flashcards/_review_start_buttons.html', takes_context=True)
def show_review_start_buttons(context):
    return _show_review_start_buttons(context)
    
    
@register.inclusion_tag(
    'flashcards/_review_start_buttons.html', takes_context=True)
def show_deck_review_start_buttons(context, deck):
    return _show_review_start_buttons(context, deck=deck)
