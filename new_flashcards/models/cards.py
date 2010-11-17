from cardtemplates import CardTemplate
from constants import GRADE_NONE, GRADE_BAD, GRADE_GOOD, GRADE_EASY
from constants import MAX_NEW_CARD_ORDINAL
from datetime import timedelta, datetime
from dbtemplates.models import Template
from django.contrib.auth.models import User
from django.db import models
from django.db import transaction
from django.template.loader import render_to_string
from facts import Fact, FactType, SharedFact
from repetitionscheduler import repetition_algo_dispatcher
from reviews import ReviewStatistics
from undo import UndoCardReview
from utils import timedelta_to_float
import random
import usertagging
from managers.cardmanager import CardManager



class Card(models.Model):
    objects = CardManager()

    fact = models.ForeignKey(Fact, db_index=True)
    template = models.ForeignKey(CardTemplate)

    #TODO how to have defaults without null (gives a 'may not be NULL' error)
    # negatives for lower priority, positives for higher
    priority = models.IntegerField(default=0, null=True, blank=True) 
    
    leech = models.BooleanField() #TODO add leech handling
    
    # False when the card is removed from the Fact. This way, we can keep 
    # card statistics if enabled later.
    active = models.BooleanField(default=True, db_index=True) 

    # Not used right now. 'active' is more like a deletion, this is lighter.
    suspended = models.BooleanField(default=False, db_index=True) 

    new_card_ordinal = models.PositiveIntegerField(null=True, blank=True)

    #for owner cards, part of synchronized decks, not used yet
    #synchronized_with = models.ForeignKey('self', null=True, blank=True) 

    ease_factor = models.FloatField(null=True, blank=True)
    interval = models.FloatField(null=True, blank=True, db_index=True) #days
    due_at = models.DateTimeField(null=True, blank=True, db_index=True)
    
    last_ease_factor = models.FloatField(null=True, blank=True)
    last_interval = models.FloatField(null=True, blank=True)
    last_due_at = models.DateTimeField(null=True, blank=True)

    last_reviewed_at = models.DateTimeField(null=True, blank=True)
    last_review_grade = models.PositiveIntegerField(null=True, blank=True)
    last_failed_at = models.DateTimeField(null=True, blank=True)
    
    #TODO use this for is_new
    review_count = models.PositiveIntegerField(default=0, editable=False) 

    class Meta:
        unique_together = (('fact', 'template'), )
        app_label = 'flashcards'

    def __unicode__(self):
        from django.utils.html import strip_tags

        field_content = dict((field_content.field_type_id, field_content,)
                             for field_content
                             in self.fact.fieldcontent_set.all())
        card_context = {'fields': field_content}
        front = render_to_string(
            self.template.front_template_name, card_context)
        back = render_to_string(
            self.template.back_template_name, card_context)

        return strip_tags(u'{0} | {1}'.format(front, back))

    @property
    def owner(self):
        return self.fact.deck.owner

    @property
    def siblings(self):
        '''Returns the other cards from this card's fact.'''
        return self.fact.card_set.exclude(id=self.id)

    @property
    def is_new(self):
        '''Returns whether this card has been reviewed before.'''
        return self.last_reviewed_at is None

    def is_mature(self):
        return self.interval >= MATURE_INTERVAL_MIN

    def is_due(self, time=None):
        '''Returns True if this card's due date is in the past.'''
        #TODO why would is_new ever be True and self.due_at be none?
        if time is None: time = datetime.utcnow()

        if self.is_new or not self.due_at:
            return False
        return self.due_at < time

    def _render(self, template_name):
        # map fieldtype-id to fieldcontents
        fields = dict((field.field_type.id, field)
                      for field in self.fact.field_contents)
        card_context = {'fields': fields}
        return render_to_string(template_name, card_context)

    def render_front(self):
        '''Returns a string of the rendered card front.'''
        return self._render(self.template.front_template_name)

    def render_back(self):
        '''Returns a string of the rendered card back.'''
        return self._render(self.template.back_template_name)

    def calculated_interval(self):
        '''
        The `interval` property of a card doesn't necessarily decide the 
        time between the review and the due date - it's used as a basis for 
        calculating the due date, but other factors may apply.

        This returns the actual time between last review and due date. This 
        is the time the user was supposed to wait.
        '''
        return self.due_at - self.last_reviewed_at

    def delay(self, duration):
        '''
        This card's due date becomes `duration` (a timedelta) days from now, 
        or from its due date if not yet due.
        
        This is for when this card is due at the same time as a sibling 
        card (a card from the same fact).
        '''
        now = datetime.utcnow()
        from_date = self.due_at if self.due_at >= now else now
        self.due_at = from_date + duration

    def _update_statistics(self, grade, reviewed_at):
        '''Updates this card's stats. Call this for each review.'''
        #TODO update CardStatistics
        card_history_item = CardHistory(
            card=self, response=grade, reviewed_at=reviewed_at)
        card_history_item.save()
        self.review_count += 1
        return card_history_item

    def _apply_updated_schedule(self, next_repetition)
        '''
        Updates this card's scheduling with values for the next repetition.

        `next_repetition` should have the following fields:
            `interval`, `ease_factor`, `due_at`

        (See `NextRepetition` class)
        '''
        self.last_ease_factor, self.ease_factor = \
            self.ease_factor, next_repetition.ease_factor
        self.last_interval, self.interval = \
            self.interval, next_repetition.interval
        self.last_due_at, self.ease_factor = \
            self.due_at, next_repetition.ease_factor

    @transaction.commit_on_success
    def review(self, grade):
        '''
        Commits a review rated with `grade`.
        '''
        reviewed_at = datetime.utcnow()
        was_new = card.is_new

        # Update this card's statistics
        card_history_item = self._update_statistics(grade, reviewed_at)

        # Update the overall review statistics for this user
        review_stats = self.owner.reviewstatistics
        if was_new:
            review_stats.increment_new_reviews()
        if grade == GRADE_NONE:
            review_stats.increment_failed_reviews()

        # Create Undo stack item
        UndoCardReview.objets.add_undo(card_history_item)

        # Compute and apply updated card repetition values
        repetition_algo = repetition_algo_dispatcher(
            self, grade, reviewed_at=reviewed_at)
        next_repetition = repetition_algo.next_repetition()
        self._update_repetition_values(next_repetition)

        self.last_review_grade = grade
        self.last_reviewed_at = reviewed_at

        if grade == GRADE_NONE:
            self.last_failed_at = reviewed_at

        review_stats.save()
        self.save()


