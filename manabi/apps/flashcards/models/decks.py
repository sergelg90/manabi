from collections import defaultdict
import datetime
import itertools

from cachecow.decorators import cached_function
from django.contrib.auth.models import User
from django.core.urlresolvers import reverse
from django.db import models
from django.db import transaction
from django.db.models.query import QuerySet

from manabi.apps.manabi_redis.models import redis
from manabi.apps.books.models import Textbook
from constants import DEFAULT_EASE_FACTOR
from manabi.apps.flashcards.cachenamespaces import deck_review_stats_namespace
import cards


class DeckQuerySet(QuerySet):
    def of_user(self, user):
        return self.filter(owner=user, active=True)

    def shared_decks(self):
        return self.filter(shared=True, active=True)

    def synchronized_decks(self, user):
        return self.filter(owner=user, synchronized_with__isnull=False)


class Deck(models.Model):
    objects = DeckQuerySet.as_manager()

    name = models.CharField(max_length=100)
    description = models.TextField(max_length=2000, blank=True)
    owner = models.ForeignKey(User, db_index=True, editable=False)

    textbook_source = models.ForeignKey(Textbook, null=True, blank=True)

    priority = models.IntegerField(default=0, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    modified_at = models.DateTimeField(auto_now=True, editable=False)

    # whether this is a publicly shared deck
    shared = models.BooleanField(default=False, blank=True)
    shared_at = models.DateTimeField(null=True, blank=True, editable=False)
    # or if not, whether it's synchronized with a shared deck
    synchronized_with = models.ForeignKey('self',
            null=True, blank=True, related_name='subscriber_decks')

    # "active" is just a soft deletion flag. "suspended" is temporarily
    # disabled.
    suspended = models.BooleanField(default=False, db_index=True)
    active = models.BooleanField(default=True, blank=True, db_index=True)

    def __unicode__(self):
        return u'{0} ({1})'.format(self.name, self.owner)

    class Meta:
        app_label = 'flashcards'
        ordering = ('name',)
        #TODO-OLD unique_together = (('owner', 'name'), )

    def get_absolute_url(self):
        return reverse('deck_detail', kwargs={'deck_id': self.id})

    def delete(self, *args, **kwargs):
        # You shouldn't delete a shared deck - just set active=False
        self.subscriber_decks.clear()
        super(Deck, self).delete(*args, **kwargs)

    def fact_tags(self):
        '''
        Returns tags for all facts inside this deck.
        Includes tags on facts made on subscribed facts.
        '''
        #return usertagging.models.Tag.objects.usage_for_queryset(
            #self.facts())
        from facts import Fact
        deck_facts = Fact.objects.deck_facts(self)
        return usertagging.models.Tag.objects.usage_for_queryset(
            deck_facts)

    @property
    def has_subscribers(self):
        '''
        Returns whether there are subscribers to this deck, because
        it is shared, or it had been shared before.
        '''
        return self.subscriber_decks.filter(active=True).exists()

    @transaction.atomic
    def share(self):
        '''
        Shares this deck publicly.
        '''
        if self.synchronized_with:
            raise TypeError('Cannot share synchronized decks (decks which are already synchronized with shared decks).')

        self.shared = True
        self.shared_at = datetime.datetime.utcnow()
        self.save()

    @transaction.atomic
    def unshare(self):
        '''
        Unshares this deck.
        '''
        if not self.shared:
            raise TypeError('This is not a shared deck, so it cannot be unshared.')

        self.shared = False
        self.save()

    def get_subscriber_deck_for_user(self, user):
        '''
        Returns the subscriber deck for `user` of this deck.
        If it doesn't exist, returns None.
        If multiple exist, even though this shouldn't happen,
        we just return the first one.
        '''
        subscriber_decks = self.subscriber_decks.filter(owner=user, active=True)

        if subscriber_decks.exists():
            return subscriber_decks[0]

    #TODO implement subscribing with new stuff.
    @transaction.atomic
    def subscribe(self, user):
        '''
        Subscribes to this shared deck for the given user.
        They will study this deck as their own, but will
        still receive updates to content.

        Returns the newly created deck.

        If the user was already subscribed to this deck,
        returns the existing deck.
        '''
        from manabi.apps.flashcards.models import Card, Fact

        # check if the user is already subscribed to this deck
        subscriber_deck = self.get_subscriber_deck_for_user(user)
        if subscriber_deck:
            return subscriber_deck

        if not self.shared:
            raise TypeError('This is not a shared deck - cannot subscribe to it.')
        if self.synchronized_with:
            raise TypeError('Cannot share a deck that is already synchronized to a shared deck.')

        #TODO-OLD dont allow multiple subscriptions to same deck by same user

        # copy the deck
        deck = Deck(
            synchronized_with=self,
            name=self.name,
            description=self.description,
            #TODO-OLD implement textbook=shared_deck.textbook, #picture too...
            priority=self.priority,  # TODO: Remove.
            textbook_source=self.textbook_source,
            owner_id=user.id)
        deck.save()

        # copy all facts
        copied_facts = []
        copied_cards = []
        for shared_fact in self.fact_set.filter(active=True).order_by('new_fact_ordinal'):
            copy_attrs = [
                'synchronized_with', 'active', 'new_fact_ordinal',
                'expression', 'reading', 'meaning', 'suspended',
            ]
            fact_kwargs = {attr: getattr(shared_fact, attr) for attr in copy_attrs}
            fact = Fact(deck=deck, **fact_kwargs)
            copied_facts.append(fact)

            # Copy the cards.
            copied_cards_for_fact = []
            for shared_card in shared_fact.card_set.filter(active=True, suspended=False):
                import sys
                print >>sys.stderr, "Copying a card...", shared_card, shared_card.id, shared_card.deck_id
                card = shared_card.copy(fact)
                copied_cards_for_fact.append(card)
            copied_cards.append(copied_cards_for_fact)

        # Persist everything.
        created_facts = Fact.objects.bulk_create(copied_facts)
        for fact, fact_cards in zip(created_facts, copied_cards):
            for fact_card in fact_cards:
                fact_card.fact_id = fact.id
        Card.objects.bulk_create(itertools.chain.from_iterable(copied_cards))

        # Done!
        return deck

    def card_count(self):
        return cards.Card.objects.of_deck(self).available().count()

    #TODO-OLD kill - unused?
    #@property
    #def new_card_count(self):
    #    return Card.objects.approx_new_count(deck=self)
    #    #FIXME do for sync'd decks
    #    return cards.Card.objects.cards_new_count(
    #            self.owner, deck=self, active=True, suspended=False)

    #TODO-OLD kill - unused?
    #@property
    #def due_card_count(self):
    #    return cards.Card.objects.cards_due_count(
    #            self.owner, deck=self, active=True, suspended=False)

    @cached_function(namespace=deck_review_stats_namespace)
    def average_ease_factor(self):
        '''
        Includes suspended cards in the calcuation. Doesn't include inactive cards.
        '''
        ease_factors = redis.zrange('ease_factor:deck:{0}'.format(self.id),
                                    0, -1, withscores=True)
        cardinality = len(ease_factors)
        if cardinality:
            return sum(score for val,score in ease_factors) / cardinality
        return DEFAULT_EASE_FACTOR

    @transaction.atomic
    def delete_cascading(self):
        for fact in self.fact_set.all():
            for card in fact.card_set.all():
                card.delete()
            fact.delete()
        self.delete()
