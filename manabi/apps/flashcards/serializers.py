import logging

from django.shortcuts import get_object_or_404
from rest_framework import serializers

from manabi.api.serializers import ManabiModelSerializer
from manabi.apps.flashcards.models import Deck, Fact, Card
from manabi.apps.manabi_auth.serializers import UserSerializer


class DeckSerializer(ManabiModelSerializer):
    owner = UserSerializer(read_only=True)

    class Meta:
        model = Deck
        fields = (
            'id',
            'name',
            'description',
            'owner',
            'card_count',
            'shared',
            'shared_at',
            'created_at',
            'modified_at',
            'suspended',
        )


class SynchronizedDeckSerializer(ManabiModelSerializer):
    owner = UserSerializer(read_only=True)
    synchronized_with = serializers.PrimaryKeyRelatedField(
        queryset=Deck.objects.shared_decks()
    )

    class Meta:
        model = Deck
        fields = (
            'owner',
            'synchronized_with',
        )

    def create(self, validated_data):
        upstream_deck = validated_data['synchronized_with']
        # upstream_deck = get_object_or_404(
        #     Deck, pk=validated_data['synchronized_with'].id)
        new_deck = upstream_deck.subscribe(validated_data['owner'])
        return new_deck


class FactSerializer(ManabiModelSerializer):
    card_count = serializers.ReadOnlyField()
    suspended = serializers.BooleanField()

    def to_representation(self, obj):
        data = super(FactSerializer, self).to_representation(obj)

        if obj.pulls_from_upstream:
            data.update({
                'expression': obj.synchronized_with.expression,
                'reading': obj.synchronized_with.reading,
                'meaning': obj.synchronized_with.meaning,
            })

        return data

    class Meta:
        model = Fact
        fields = (
            'id',
            'deck',
            'active',
            'suspended',
            'expression',
            'card_count',
            'reading',
            'meaning',
            'created_at',
            'modified_at',
        )

    def update(self, instance, validated_data):
        suspended = validated_data.pop('suspended', None)

        fact = super(FactSerializer, self).update(instance, validated_data)

        if suspended is not None and suspended != fact.suspended:
            if suspended:
                fact.suspend()
            else:
                fact.unsuspend()

        return fact


class FactWithCardsSerializer(FactSerializer):
    active_card_templates = serializers.MultipleChoiceField(
        choices=['recognition', 'kanji_reading', 'kanji_writing'],
        allow_empty=True,
    )

    class Meta(FactSerializer.Meta):
        fields = FactSerializer.Meta.fields + (
            'active_card_templates',
        )

    def create(self, validated_data):
        active_card_templates = validated_data.pop('active_card_templates')
        fact = super(FactWithCardsSerializer, self).create(validated_data)
        fact.set_active_card_templates(active_card_templates)
        return fact

    def update(self, instance, validated_data):
        active_card_templates = validated_data.pop('active_card_templates', None)

        fact = super(FactWithCardsSerializer, self).update(instance, validated_data)

        if active_card_templates is not None:
            fact.set_active_card_templates(active_card_templates)

        return fact


class DetailedFactSerializer(FactWithCardsSerializer):
    deck = DeckSerializer()


class CardSerializer(ManabiModelSerializer):
    expression = serializers.CharField(source='fact.expression')
    reading = serializers.CharField(source='fact.reading')
    meaning = serializers.CharField(source='fact.meaning')

    class Meta:
        model = Card
        fields = (
            'id',
            'deck',
            'fact',
            'ease_factor',
            'interval',
            'due_at',
            'last_ease_factor',
            'last_interval',
            'last_due_at',
            'review_count',
            'template',
            'expression',
            'reading',
            'meaning',
        )


class ReviewAvailabilitiesSerializer(serializers.Serializer):
    ready_for_review = serializers.BooleanField()
    next_new_cards_count = serializers.IntegerField()
    early_review_available = serializers.BooleanField()
    # new_cards_available = serializers.BooleanField()

    class Meta:
        read_only_fields = (
            'ready_for_review',
            'next_new_cards_count',
            'early_review_available',
        )


class ReviewInterstitialSerializer(serializers.Serializer):
    review_availabilities = ReviewAvailabilitiesSerializer(required=True)

    class Meta:
        read_only_fields = (
            'review_availabilities',
        )


class NextCardsForReviewSerializer(serializers.Serializer):
    cards = CardSerializer(many=True)

    # `None` means it should not display an interstitial, and should continue
    # requesting the next cards for review.
    interstitial = ReviewInterstitialSerializer(required=False)

    class Meta:
        read_only_fields = (
            'cards',
            'interstitial',
        )
