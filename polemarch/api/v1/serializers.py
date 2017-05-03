# pylint: disable=no-member,unused-argument
from __future__ import unicode_literals
import json

import six
from django.contrib.auth.models import User

from rest_framework import serializers
from rest_framework import exceptions
from rest_framework.response import Response

from ...main import models


# Serializers field for usability
class ModelRelatedField(serializers.PrimaryKeyRelatedField):
    def __init__(self, **kwargs):
        model = kwargs.pop("model", None)
        assert not ((model is not None or self.queryset is not None) and
                    kwargs.get('read_only', None)), (
            'Relational fields should not provide a `queryset` or `model`'
            ' argument, when setting read_only=`True`.'
        )
        if model is not None:
            kwargs["queryset"] = model.objects.all()
        super(ModelRelatedField, self).__init__(**kwargs)


class DictField(serializers.CharField):
    def to_internal_value(self, data):
        tstr = isinstance(data, (six.string_types, six.text_type))
        tdict = isinstance(data, dict)
        return data if tdict or tstr else self.fail("Unknown type.")

    def to_representation(self, value):
        return json.loads(value) if not isinstance(value, dict) else value


# Serializers
class UserSerializer(serializers.ModelSerializer):

    class UserExist(exceptions.ValidationError):
        status_code = 409

    class Meta:
        model = User
        fields = ('id',
                  'username',
                  'is_active',
                  'url',)
        read_only_fields = ('is_staff',
                            'is_superuser',)

    def create(self, data):
        if not self.context['request'].user.is_staff:
            raise exceptions.PermissionDenied
        valid_fields = ['username', 'password',
                        "email", "first_name", "last_name"]
        creditals = {d: data[d] for d in valid_fields if data.get(d, False)}
        raw_passwd = self.initial_data.get("raw_password", "False")
        user = super(UserSerializer, self).create(creditals)
        if not raw_passwd == "True":
            user.set_password(creditals['password'])
            user.save()
        user.related_objects.get_or_create()
        return user

    def is_valid(self, raise_exception=False):
        if self.instance is None:
            try:
                User.objects.get(username=self.initial_data['username'])
                raise self.UserExist({'username': ["Already exists."]})
            except User.DoesNotExist:
                pass
        return super(UserSerializer, self).is_valid(raise_exception)

    def update(self, instance, validated_data):
        if not self.context['request'].user.is_staff and \
                        instance.id != self.context['request'].user.id:
            raise exceptions.PermissionDenied
        instance.username = validated_data.get('username',
                                               instance.username)
        instance.is_active = validated_data.get('is_active',
                                                instance.is_active)
        instance.email = validated_data.get('email',
                                            instance.email)
        instance.first_name = validated_data.get('first_name',
                                                 instance.first_name)
        instance.last_name = validated_data.get('last_name',
                                                instance.last_name)
        instance.is_staff = validated_data.get('is_staff',
                                               instance.is_staff)
        if validated_data.get('password', False):
            instance.set_password(validated_data.get('password', None))
        instance.save()
        return instance


class OneUserSerializer(UserSerializer):
    raw_password = serializers.HiddenField(default=False, initial=False)

    class Meta:
        model = User
        fields = ('id',
                  'username',
                  'password',
                  'raw_password',
                  'is_active',
                  'is_staff',
                  'first_name',
                  'last_name',
                  'email',
                  'url',)
        read_only_fields = ('is_superuser',
                            'date_joined',)


class EnvironmentSerializer(serializers.ModelSerializer):
    data = DictField(required=False)
    hosts = serializers.HyperlinkedRelatedField(many=True,
                                                read_only=True,
                                                view_name='host-detail')

    class Meta:
        model = models.Environment
        fields = ('id',
                  'name',
                  'type',
                  'key',
                  'data',
                  'hosts')


class VariableSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.Variable
        fields = ('key',
                  'value',)

    def to_representation(self, instance):
        return {instance.key: instance.value}


class _WithVariablesSerializer(serializers.ModelSerializer):
    operations = dict(DELETE="remove",
                      POST="add",
                      PUT="set")

    def _response(self, total, found, code=200):
        data = dict(total=len(total))
        data["operated"] = len(found)
        data["not_found"] = data["total"] - data["operated"]
        return Response(data, status=code)

    def __do_with_vars(self, method_name, *args, **kwargs):
        method = getattr(super(_WithVariablesSerializer, self), method_name)
        variables = kwargs['validated_data'].pop("vars", None)
        instance = method(*args, **kwargs)
        if variables is not None:
            if isinstance(variables, (six.string_types, six.text_type)):
                variables = json.loads(variables)
            instance.set_vars(variables)
        if method.__name__ == "create":
            user = self.context['request'].user
            instance.related_objects.add(
                models.TypesPermissions.objects.get_or_create(user=user)[0]
            )
        return instance

    def _operate(self, request, attr, obj_list):
        tp = getattr(self.instance, attr)
        action = self.operations[request.method]
        if action == "set":
            # Because django<=1.9 does not support .set()
            getattr(tp, "clear")()
            action = "add"
        getattr(tp, action)(*obj_list)
        return self._response(request.data, obj_list)

    def create(self, validated_data):
        return self.__do_with_vars("create", validated_data=validated_data)

    def update(self, instance, validated_data):
        return self.__do_with_vars("update", instance,
                                   validated_data=validated_data)

    def permissions(self, request):
        pms = models.TypesPermissions.objects.filter(user__id__in=request.data)
        return self._operate(request, "related_objects", pms)


class HostSerializer(_WithVariablesSerializer):
    vars = DictField(required=False, write_only=True)
    environment = ModelRelatedField(required=False,
                                    model=models.Environment)

    class Meta:
        model = models.Host
        fields = ('id',
                  'name',
                  'type',
                  'environment',
                  'vars',
                  'url',)


class OneHostSerializer(HostSerializer):
    vars = DictField(required=False)

    class Meta:
        model = models.Host
        fields = ('id',
                  'name',
                  'type',
                  'environment',
                  'vars',
                  'url',)


class TaskSerializer(_WithVariablesSerializer):

    class Meta:
        model = models.Task
        fields = ('id',
                  'name',
                  'url',)


class OneTaskSerializer(TaskSerializer):
    project  = ModelRelatedField(read_only=True)
    playbook = serializers.CharField(read_only=True)

    class Meta:
        model = models.Task
        fields = ('id',
                  'name',
                  'playbook',
                  'project',
                  'url',)


class PeriodicTaskSerializer(_WithVariablesSerializer):

    class Meta:
        model = models.PeriodicTask
        fields = ('id',
                  'type',
                  'schedule',
                  'playbook',
                  'url',)


class OnePeriodicTaskSerializer(PeriodicTaskSerializer):

    class Meta:
        model = models.PeriodicTask
        fields = ('id',
                  'type',
                  'schedule',
                  'playbook',
                  'project',
                  'url',)

###################################
# Subclasses for operations
# with hosts and groups


class _InventoryOperations(_WithVariablesSerializer):

    def _get_objects(self, model, objs_id):
        user = self.context['request'].user
        qs = model.objects.all()
        if not user.is_staff:
            qs = qs.filter(related_objects__user=user)
        return list(qs.filter(id__in=objs_id))

    def get_operation(self, request, attr):
        tp = getattr(self.instance, attr)
        obj_list = self._get_objects(tp.model, request.data)
        return self._operate(request, attr, obj_list)

    def hosts_operations(self, request):
        return self.get_operation(request, attr="hosts")

    def groups_operations(self, request):
        return self.get_operation(request, attr="groups")


###################################


class GroupSerializer(_WithVariablesSerializer):
    vars = DictField(required=False, write_only=True)

    class Meta:
        model = models.Group
        fields = ('id',
                  'name',
                  'vars',
                  'children',
                  'url',)


class OneGroupSerializer(GroupSerializer, _InventoryOperations):
    vars   = DictField(required=False)
    hosts  = HostSerializer(read_only=True, many=True)
    groups = GroupSerializer(read_only=True, many=True)

    class Meta:
        model = models.Group
        fields = ('id',
                  'name',
                  'hosts',
                  "groups",
                  'vars',
                  'children',
                  'url',)

    class ValidationException(exceptions.ValidationError):
        status_code = 409

    def hosts_operations(self, request):
        if self.instance.children:
            raise self.ValidationException("Group is children.")
        return super(OneGroupSerializer, self).hosts_operations(request)

    def groups_operations(self, request):
        if not self.instance.children:
            raise self.ValidationException("Group is not children.")
        return super(OneGroupSerializer, self).groups_operations(request)


class InventorySerializer(_WithVariablesSerializer):
    vars = DictField(required=False, write_only=True)

    class Meta:
        model = models.Inventory
        fields = ('id',
                  'name',
                  'vars',
                  'url',)


class OneInventorySerializer(InventorySerializer, _InventoryOperations):
    vars   = DictField(required=False)
    hosts  = HostSerializer(read_only=True, many=True)
    groups = GroupSerializer(read_only=True, many=True)

    class Meta:
        model = models.Inventory
        fields = ('id',
                  'name',
                  'hosts',
                  "groups",
                  'vars',
                  'url',)


class ProjectSerializer(_WithVariablesSerializer):

    class Meta:
        model = models.Project
        fields = ('id',
                  'name',
                  'url',)


class OneProjectSerializer(ProjectSerializer, _InventoryOperations):
    hosts       = HostSerializer(read_only=True, many=True)
    groups      = GroupSerializer(read_only=True, many=True)
    inventories = InventorySerializer(read_only=True, many=True)

    class Meta:
        model = models.Project
        fields = ('id',
                  'name',
                  'repository',
                  'hosts',
                  "groups",
                  'inventories',
                  'url',)

    def inventories_operations(self, request):
        return self.get_operation(request, attr="inventories")
