from django.contrib import admin
from unfold.admin import ModelAdmin, StackedInline
from django import forms
from django.urls import reverse
from django.utils.html import format_html
from django.contrib.contenttypes.models import ContentType
from simple_history.admin import SimpleHistoryAdmin

from agent.mixins import AdminActionsMixin
from agent.models import AgentModel, Agent, GoogleApiKey, GoogleVoice, Prompt, TokenUsage, AgentProfile, Message, AgentApiKey, PromptCategory, MessagePart
from agent.utils import get_genai_client
from django.contrib.auth.models import User, Group
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.models import User, Group
from agent.admin_utils import AjaxTaskModelAdmin
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm
from unfold.admin import ModelAdmin
from agent.sections import MessageHistorySection, MessagePartSection, MessageInstructionsSection, MessageInputsSection, MessageOutputsSection
from django.contrib import admin

class AjaxSectionAdminMixin:
    def get_urls(self):
        urls = super().get_urls()
        custom_urls = []
        
        # Discover and add URLs from sections
        if hasattr(self, "list_sections"):
            for section_class in self.list_sections:
                if hasattr(section_class, "get_section_urls"):
                    section_instance = section_class(request=None, instance=None)
                    section_urls = section_instance.get_section_urls(self)
                    custom_urls.extend(section_urls)
        return custom_urls + urls

    def ajax_section_update_view(self, request):
        # This is a placeholder. If MessageAdmin needs field updates via AJAX,
        # this method would need to be implemented fully, similar to scene/admin.py.
        from django.http import JsonResponse
        return JsonResponse({"status": "success", "message": "Update handled."})


@admin.register(MessagePart)
class MessagePartAdmin(ModelAdmin):
    list_display = ('id', 'message', 'part_type', 'key', 'text', 'json', 'created_at')
    list_filter = ('part_type', 'message__agent', 'message__preset')
    search_fields = ('text', 'key')
    autocomplete_fields = ('message',)


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

@admin.register(PromptCategory)
class PromptCategoryAdmin(ModelAdmin):
    list_display = ('name', 'slug' )
    list_display_links = ('name', 'slug')
    search_fields = ("name",)

    

@admin.register(Prompt)
class PromptAdmin(SimpleHistoryAdmin, ModelAdmin):
    list_display = ('name', 'is_global', 'category', 'display_categories', 'display_content_types', 'order')
    list_filter = ('category', 'content_types')
    list_display_links = ('name',)
    autocomplete_fields = ('content_types', 'categories')
    search_fields = ("name",)
    ordering_field = "order"

    def display_content_types(self, obj):
        return ", ".join([ct.model for ct in obj.content_types.all()])
    display_content_types.short_description = "Content Types"

    def display_categories(self, obj):
        categories = obj.categories.all()
        if not categories.exists():
            return "-"
        from django.utils.safestring import mark_safe
        badges = [
            format_html(
                '<a href="{}" class="bg-primary-100 text-primary-800 dark:bg-primary-900/30 dark:text-primary-400 border border-primary-200 dark:border-primary-800/30 px-2 py-0.5 rounded-md text-xs font-medium inline-block hover:underline">{}</a>',
                reverse("admin:agent_promptcategory_changelist") + f"?id__exact={cat.id}",
                cat.name
            )
            for cat in categories
        ]
        return mark_safe(" ".join(badges))
    display_categories.short_description = "Categories"
    

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
class MessageAdmin(AjaxSectionAdminMixin, AjaxTaskModelAdmin):
    list_display = ('id', 'content_object_link', 'agent','preset', 'target_field', 'input_parts', 'output_parts', 'instruction_parts', 'last_tasks', 'created_at')
    search_fields = ('id', 'preset', 'target_field', 'agent__name', 'user__username')
    readonly_fields = ('created_at',)
    list_sections = [
        MessageHistorySection,
        MessageInstructionsSection,
        MessageInputsSection,
        MessageOutputsSection,
    ]

    def content_object_link(self, obj):
        if not obj.content_object:
            return "-"
        content_type = obj.content_type
        url = ( # The ContentType model has a 'model' attribute, not 'model_name'.
            reverse(f"admin:{content_type.app_label}_{content_type.model}_changelist")
            + f"?id__exact={obj.object_id}"
        )
        return format_html('<a href="{}" class="text-primary-600 font-medium hover:underline">{}</a>', url, str(obj.content_object))
    content_object_link.short_description = "Content Object"

    def input_parts(self, obj):
        count = obj.parts.filter(part_type=MessagePart.PART_TYPE_INPUT).count()
        url = (
            reverse("admin:agent_messagepart_changelist")
            + f"?message__id__exact={obj.id}&part_type__exact={MessagePart.PART_TYPE_INPUT}"
        )
        return format_html('<a href="{}">{} Inputs</a>', url, count)
    input_parts.short_description = "Inputs"

    def output_parts(self, obj):
        count = obj.parts.filter(part_type=MessagePart.PART_TYPE_OUTPUT).count()
        url = (
            reverse("admin:agent_messagepart_changelist")
            + f"?message__id__exact={obj.id}&part_type__exact={MessagePart.PART_TYPE_OUTPUT}"
        )
        return format_html('<a href="{}">{} Outputs</a>', url, count)
    output_parts.short_description = "Outputs"

    def instruction_parts(self, obj):
        count = obj.parts.filter(part_type=MessagePart.PART_TYPE_INSTRUCTIONS).count()
        url = (
            reverse("admin:agent_messagepart_changelist")
            + f"?message__id__exact={obj.id}&part_type__exact={MessagePart.PART_TYPE_INSTRUCTIONS}"
        )
        return format_html('<a href="{}">{} Instructions</a>', url, count)
    instruction_parts.short_description = "Instructions"

    def has_change_permission(self, request, obj=None):
        if not obj:
            return True
        return obj.user == request.user or request.user.is_superuser
