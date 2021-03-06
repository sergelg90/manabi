from django.contrib.auth.models import User
from rest_framework import serializers

from manabi.api.serializers import ManabiModelSerializer


class UserSerializer(ManabiModelSerializer):
    class Meta:
        model = User
        fields = (
            'id',
            'username',
            'date_joined',
        )
