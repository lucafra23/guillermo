from django.contrib import admin
from unfold.admin import ModelAdmin, StackedInline
from django import forms
from django.contrib.contenttypes.models import ContentType

from agent.mixins import AdminActionsMixin
from agent.models import AgentModel, Agent, GoogleApiKey, GoogleVoice, Prompt, TokenUsage, AgentProfile, Message, AgentApiKey
from agent.utils import get_genai_client
from django.contrib.auth.models import User, Group
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.models import User, Group

from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm
from unfold.admin import ModelAdmin

from django.contrib import admin
from unfold.admin import ModelAdmin


admin.site.unregister(User)
admin.site.unregister(Group)

@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    # Forms loaded from `unfold.forms`
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, ModelAdmin):
    pass


@admin.register(ContentType)
class ContentTypeAdmin(ModelAdmin):
    search_fields = ("model", "app_label")


@admin.action(description="List available genai models")
def list_models(modeladmin, request, queryset):
    client = get_genai_client()
    models = client.models.list()
    for model in models:
        if not AgentModel.objects.filter(name=model.name).exists():
            AgentModel.objects.create(name=model.name)
            modeladmin.message_user(request, "Model '{}' created.".format(model.name))

@admin.register(AgentModel)
class AgentModelAdmin(ModelAdmin):
    list_display = ('name', )
    list_display_links = ('name',)
    actions = [list_models]

@admin.register(Prompt)
class PromptAdmin(ModelAdmin):
    list_display = ('id', 'name', 'is_global', 'category', 'display_content_types')
    list_filter = ('category', 'content_types')
    list_display_links = ('id',)
    autocomplete_fields = ('content_types',)
    search_fields = ("name",)

    def display_content_types(self, obj):
        return ", ".join([ct.model for ct in obj.content_types.all()])
    display_content_types.short_description = "Content Types"


@admin.register(Agent)
class AgentAdmin(ModelAdmin,):
    list_display = ('name', 'output_type', 'schema')
    list_display_links = ('name',)
    search_fields = ['name']

@admin.register(GoogleVoice)
class GoogleVoiceAdmin(AdminActionsMixin, ModelAdmin):
    list_display = ('id', 'name', 'description')
    list_display_links = ('name', 'description')
    actions = ['clone']
    search_fields = ("name",)


class GoogleApiKeyForm(forms.ModelForm):
    api_key_display = forms.CharField(
        label="API Key",
        disabled=True,
        required=False,
        help_text="API Key is write-only for security and will not be displayed."
    )

    class Meta:
        model = GoogleApiKey
        fields = '__all__'
        exclude = ('api_key',)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields['api_key_display'].initial = '********' if self.instance.api_key else 'Not Set'
        else:
            # For new objects, use the real api_key field
            self.fields['api_key'] = forms.CharField(widget=forms.PasswordInput(render_value=False), required=True)

@admin.register(GoogleApiKey)
class GoogleApiKeyAdmin(ModelAdmin):
    list_display = ('name', 'user')
    form = GoogleApiKeyForm
    autocomplete_fields = ['user']
    search_fields = ['name', 'user__username']

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        # Users only see themselves
        return qs.filter(id=request.user.id)

    def get_fieldsets(self, request, obj=None):
        if obj:  # Editing an existing object
            return (
                (None, {'fields': ('name', 'user', 'enterprise', 'project', 'api_key_display')}),
            )
        # Creating a new object
        return (
            (None, {'fields': ('name', 'user', 'enterprise', 'project', 'api_key')}),
        )
    def has_change_permission(self, request, obj=None):
        if not obj:
            return True
        return obj.user == request.user or request.user.is_superuser


class AgentApiKeyInline(StackedInline):
    model = AgentApiKey
    autocomplete_fields = ['agent', 'api_key']
    extra = 0


@admin.register(AgentProfile)
class AgentProfileAdmin(ModelAdmin):
    list_display = ('user', 'credits')
    list_display_links = ('user',)
    inlines = [AgentApiKeyInline]
    autocomplete_fields = ['user']
    
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        # Users only see themselves
        return qs.filter(id=request.user.id)

    def has_change_permission(self, request, obj=None):
        if not obj:
            return True
        return obj.user == request.user or request.user.is_superuser


@admin.register(TokenUsage)
class TokenUsageAdmin(ModelAdmin):
    list_display = ('id', 'user', 'agent', 'tokens', 'preset', 'content_object', 'created')
    list_display_links = ('id',)
    autocomplete_fields = ['user']

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        # Users only see themselves
        return qs.filter(user=request.user)

@admin.register(Message)
class MessageAdmin(ModelAdmin):
    list_display = ('id', 'content_object', 'agent', 'target_field', 'created_at')
    readonly_fields = ('created_at',)

    def has_change_permission(self, request, obj=None):
        if not obj:
            return True
        return obj.user == request.user or request.user.is_superuser
