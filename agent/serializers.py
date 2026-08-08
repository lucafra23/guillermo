from rest_framework import serializers

def get_generic_serializer(model_class):
    """Dynamically creates a ModelSerializer for the given model class."""
    class GenericSerializer(serializers.ModelSerializer):
        class Meta:
            model = model_class
            fields = '__all__'
    return GenericSerializer