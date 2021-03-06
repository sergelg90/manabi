import datetime

from cachecow.decorators import cached_function
from django.contrib.auth.models import User
from django.core.urlresolvers import reverse
from django.db import (
    models,
    transaction,
)
from django.db.models.query import QuerySet

from manabi.apps.books.models import Textbook
from manabi.apps.flashcards.cachenamespaces import deck_review_stats_namespace
from manabi.apps.flashcards.models import cards
from manabi.apps.flashcards.models.constants import DEFAULT_EASE_FACTOR
from manabi.apps.flashcards.models.synchronization import copy_facts_to_subscribers
from manabi.apps.manabi_redis.models import redis


class DeckQuerySet(QuerySet):
    def of_user(self, user):
        if not user.is_authenticated():
            return self.none()

        return self.filter(owner=user, active=True)

    def shared_decks(self):
        return self.filter(shared=True, active=True)

    def synchronized_decks(self, user):
        if not user.is_authenticated():
            return self.none()

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

    @property
    def is_synchronized(self):
        return self.synchronized_with is not None

    def get_absolute_url(self):
        return reverse('deck_detail', kwargs={'deck_id': self.id})

    @transaction.atomic
    def delete(self, *args, **kwargs):
        '''
        Soft deletes without propagating anything to subscribers.
        '''
        from manabi.apps.flashcards.models import Card

        self.active = False
        self.save(update_fields=['active'])

        self.facts.update(active=False)
        self.card_set.update(active=False)

        self.subscriber_decks.clear()

        for fact in self.facts.iterator():
            fact.subscriber_facts.clear()
        self.facts.update(active=False)

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
        Shares this deck publicly. Resynchronizes with any preexisting
        subscribers.
        '''
        if self.synchronized_with:
            raise TypeError(
                "Cannot share synchronized decks (decks which are already "
                "synchronized with shared decks).")

        self.shared = True
        self.shared_at = datetime.datetime.utcnow()
        self.save(update_fields=['shared', 'shared_at'])

        copy_facts_to_subscribers(self.facts.filter(active=True))

    @transaction.atomic
    def unshare(self):
        '''
        Unshares this deck.
        '''
        if not self.shared:
            raise TypeError("This is not a shared deck, so it cannot be unshared.")

        self.shared = False
        self.save(update_fields=['shared'])

    def get_subscriber_deck_for_user(self, user):
        '''
        Returns the subscriber deck for `user` of this deck.
        If it doesn't exist, returns None.
        If multiple exist, even though this shouldn't happen,
        we just return the first one.
        '''
        subscriber_decks = self.subscriber_decks.filter(owner=user, active=True)
        return subscriber_decks.first()

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

        # Check if the user is already subscribed to this deck.
        subscriber_deck = self.get_subscriber_deck_for_user(user)
        if subscriber_deck:
            return subscriber_deck

        if not self.shared:
            raise TypeError('This is not a shared deck - cannot subscribe to it.')
        if self.synchronized_with:
            raise TypeError('Cannot share a deck that is already synchronized to a shared deck.')

        #TODO-OLD dont allow multiple subscriptions to same deck by same user

        deck = Deck.objects.create(
            synchronized_with=self,
            name=self.name,
            description=self.description,
            priority=self.priority,  # TODO: Remove.
            textbook_source=self.textbook_source,
            owner_id=user.id,
        )

        copy_facts_to_subscribers(self.facts.all(), subscribers=[user])

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
