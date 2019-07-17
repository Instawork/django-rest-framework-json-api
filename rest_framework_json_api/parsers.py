"""
Parsers
"""
from django.utils import six
from rest_framework import parsers
from rest_framework.exceptions import ParseError

from . import exceptions, renderers, serializers, utils
from .settings import json_api_settings


class JSONParser(parsers.JSONParser):
    """
    Similar to `JSONRenderer`, the `JSONParser` you may override the following methods if you
    need highly custom parsing control.

    A JSON API client will send a payload that looks like this:

    .. code:: json


        {
            "data": {
                "type": "identities",
                "id": 1,
                "attributes": {
                    "first_name": "John",
                    "last_name": "Coltrane"
                }
            }
        }

    We extract the attributes so that DRF serializers can work as normal.
    """
    media_type = 'application/vnd.api+json'
    renderer_class = renderers.JSONRenderer

    @staticmethod
    def parse_attributes(data):
        attributes = data.get('attributes')
        uses_format_translation = json_api_settings.format_type

        if not attributes:
            return dict()
        elif uses_format_translation:
            # convert back to python/rest_framework's preferred underscore format
            return utils._format_object(attributes, 'underscore')
        else:
            return attributes

    @staticmethod
    def parse_relationships(data):
        uses_format_translation = json_api_settings.format_type
        relationships = data.get('relationships')

        if not relationships:
            relationships = dict()
        elif uses_format_translation:
            # convert back to python/rest_framework's preferred underscore format
            relationships = utils._format_object(relationships, 'underscore')

        # Parse the relationships
        parsed_relationships = dict()
        for field_name, field_data in relationships.items():
            field_data = field_data.get('data')
            if isinstance(field_data, dict) or field_data is None:
                parsed_relationships[field_name] = field_data
            elif isinstance(field_data, list):
                parsed_relationships[field_name] = list(relation for relation in field_data)
        return parsed_relationships

    @staticmethod
    def parse_metadata(result):
        """
        Returns a dictionary which will be merged into parsed data of the request. By default,
        it reads the `meta` content in the request body and returns it in a dictionary with
        a `_meta` top level key.
        """
        metadata = result.get('meta')
        if metadata:
            return {'_meta': metadata}
        else:
            return {}

    def parse(self, stream, media_type=None, parser_context=None):
        """
        Parses the incoming bytestream as JSON and returns the resulting data
        """
        result = super(JSONParser, self).parse(
            stream, media_type=media_type, parser_context=parser_context
        )

        if not isinstance(result, dict) or 'data' not in result:
            raise ParseError('Received document does not contain primary data')

        data = result.get('data')
        view = parser_context['view']

        from rest_framework_json_api.views import RelationshipView
        if isinstance(view, RelationshipView):
            # We skip parsing the object as JSONAPI Resource Identifier Object and not a regular
            # Resource Object
            if isinstance(data, list):
                for resource_identifier_object in data:
                    if not (
                        resource_identifier_object.get('id') and
                        resource_identifier_object.get('type')
                    ):
                        raise ParseError(
                            'Received data contains one or more malformed JSONAPI '
                            'Resource Identifier Object(s)'
                        )
            elif not (data.get('id') and data.get('type')):
                raise ParseError('Received data is not a valid JSONAPI Resource Identifier Object')

            return data

        request = parser_context.get('request')

        # Check for inconsistencies
        parsed_data = []
        if not isinstance(data, list):
            data = [data]
        for datum in data:
            if request.method in ('PUT', 'POST', 'PATCH'):
                resource_name = utils.get_resource_name(
                    parser_context, expand_polymorphic_types=True)

                if isinstance(resource_name, six.string_types):
                    if datum.get('type') != resource_name:
                        raise exceptions.Conflict(
                            "The resource object's type ({datum_type}) is not the type that "
                            "constitute the collection represented by the endpoint "
                            "({resource_type}).".format(
                                datum_type=datum.get('type'),
                                resource_type=resource_name))
                else:
                    if datum.get('type') not in resource_name:
                        raise exceptions.Conflict(
                            "The resource object's type ({datum_type}) is not the type that "
                            "constitute the collection represented by the endpoint "
                            "(one of [{resource_types}]).".format(
                                datum_type=datum.get('type'),
                                resource_types=", ".join(resource_name)))
            if not datum.get('id') and request.method in ('PATCH', 'PUT'):
                raise ParseError("The resource identifier object must contain an 'id' member")

            # Construct the return datum
            serializer_class = getattr(view, 'serializer_class', None)
            parsed_datum = {'id': datum.get('id')} if 'id' in datum else {}
            # `type` field needs to be allowed in none polymorphic serializers
            if serializer_class is not None:
                if issubclass(serializer_class, serializers.PolymorphicModelSerializer):
                    parsed_datum['type'] = datum.get('type')
            parsed_datum.update(self.parse_attributes(datum))
            parsed_datum.update(self.parse_relationships(datum))
            parsed_datum.update(self.parse_metadata(result))
            parsed_data.append(parsed_datum)

        if len(parsed_data) == 1:
            return parsed_data[0]
        return parsed_data
