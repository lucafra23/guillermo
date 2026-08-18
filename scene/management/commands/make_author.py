"""Give someone an account that can actually author a book.

WHY THIS EXISTS

Django grants a new `is_staff` user no model permissions at all, so the account a maintainer
creates and hands over opens an admin with nothing in it: every changelist answers 403 and the
index lists zero models. Nothing about that says "you need permissions" -- it looks like an
empty instance, or a broken one.

The gap is invisible to whoever is issuing the account, because they are almost always a
superuser, for whom every page works. So the failure lands entirely on the person who cannot
fix it, on their first visit, with no way to tell a missing grant from a missing book.

This turns the handover into one command, and prints exactly what it granted so the answer to
"what can they see?" is a fact rather than a memory.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

AUTHOR_GROUP = "Authors"

# What authoring a book actually touches. Kept as an explicit list rather than "every model in
# the app": an author writes and renders a book, and does not need to hand out API keys or edit
# other people's contact requests to do it.
AUTHORED_MODELS = [
    ("scene", m) for m in (
        "story", "scene", "action", "comicaction", "videoaction", "voiceaction",
        "character", "background", "prop", "style", "voice", "preset",
        "render", "renderitem", "actionorganizer", "sceneorganizer",
        "sync", "syncitem", "storyprofile", "author",
    )
]
# Read-only: useful context while authoring, not the author's to change.
READ_ONLY_MODELS = [("agent", m) for m in ("agent", "prompt", "tokenusage", "agentprofile")]

FULL = ("add", "change", "delete", "view")
READ = ("view",)


class Command(BaseCommand):
    help = ("Create or promote a user who can author books, granting the permissions an "
            "authoring account needs. Idempotent.")

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("--email", default="")
        parser.add_argument("--password", help="Set a password. Omitted, an existing user keeps "
                                               "theirs and a new one is created unusable.")
        parser.add_argument("--story", help="Name of the story to select in their profile.")
        parser.add_argument("--superuser", action="store_true",
                            help="Grant superuser instead of the authoring group.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Say what would change and change nothing.")

    def handle(self, *args, **options):
        User = get_user_model()
        username = options["username"]
        dry = options["dry_run"]

        with transaction.atomic():
            user, created = User.objects.get_or_create(
                username=username, defaults={"email": options["email"] or ""})
            self.stdout.write(f"{'created' if created else 'found'} user {username!r}")

            if options["password"]:
                user.set_password(options["password"])
            elif created:
                user.set_unusable_password()
                self.stdout.write(self.style.WARNING(
                    "  no --password given: the account cannot log in until one is set"))

            user.is_staff = True
            if options["superuser"]:
                user.is_superuser = True
            user.save()

            group, made = self._author_group()
            if options["superuser"]:
                self.stdout.write("  granted superuser (every permission, including other "
                                  "people's data)")
            else:
                user.groups.add(group)
                self.stdout.write(
                    f"  {'created' if made else 'reused'} group {AUTHOR_GROUP!r} with "
                    f"{group.permissions.count()} permissions, and added {username!r} to it")

            self._select_story(user, options.get("story"))

            # The profile the admin dereferences on every changelist. Created by a signal on new
            # users; get_or_create covers accounts that predate it, which is the case this whole
            # command exists to serve.
            from scene.models import StoryProfile
            _profile, profile_made = StoryProfile.objects.get_or_create(user=user)
            if profile_made:
                self.stdout.write("  created the missing StoryProfile")

            # The agent profile is where the user's API key hangs, and every generation reads
            # it. Both profiles come from signals on user creation, so this only matters for
            # accounts that predate the signal being connected -- which is exactly the account
            # this command is most often pointed at.
            try:
                from agent.models import AgentProfile
                _agent_profile, agent_made = AgentProfile.objects.get_or_create(user=user)
                if agent_made:
                    self.stdout.write("  created the missing AgentProfile")
            except Exception as e:                     # a deployment without the agent app
                self.stderr.write(f"  could not ensure an AgentProfile: {e}")

            if dry:
                # Marked at the very END: set_rollback breaks the transaction immediately, so
                # calling it earlier makes every remaining query raise
                # TransactionManagementError instead of reporting what a real run would do.
                self.stdout.write(self.style.WARNING("dry run: nothing was saved"))
                transaction.set_rollback(True)
                return

        self.stdout.write(self.style.SUCCESS(
            f"{username!r} can now open the admin and author. "
            f"They still need their own API key set on their agent profile before generating."))

    def _author_group(self):
        group, made = Group.objects.get_or_create(name=AUTHOR_GROUP)
        wanted = []
        for app_label, model in AUTHORED_MODELS:
            wanted += self._perms(app_label, model, FULL)
        for app_label, model in READ_ONLY_MODELS:
            wanted += self._perms(app_label, model, READ)
        if not wanted:
            raise CommandError(
                "No permissions found for the authoring models. Run migrations first: "
                "permissions are created by the post_migrate hook.")
        # set(), not add(): the group is DECLARED here, so a permission removed from the list
        # above is actually removed from the group rather than lingering from an older run.
        group.permissions.set(wanted)
        return group, made

    def _perms(self, app_label, model, actions):
        ct = ContentType.objects.filter(app_label=app_label, model=model).first()
        if ct is None:
            # A model that does not exist on this deployment is skipped loudly rather than
            # failing the whole grant: branches differ, and a partial grant beats no account.
            self.stderr.write(f"  no content type for {app_label}.{model}; skipped")
            return []
        found = list(Permission.objects.filter(
            content_type=ct, codename__in=[f"{a}_{model}" for a in actions]))
        missing = {f"{a}_{model}" for a in actions} - {p.codename for p in found}
        if missing:
            self.stderr.write(f"  {app_label}.{model}: missing {sorted(missing)}")
        return found

    def _select_story(self, user, story_name):
        if not story_name:
            return
        from scene.models import Story, StoryProfile

        story = Story.objects.filter(name=story_name).first()
        if not story:
            names = list(Story.objects.values_list("name", flat=True)[:10])
            raise CommandError(
                f"No story named {story_name!r}. Known: {', '.join(names) or '(none)'}")
        profile, _ = StoryProfile.objects.get_or_create(user=user)
        profile.story = story
        profile.save(update_fields=["story"])
        self.stdout.write(f"  selected story {story.name!r} in their profile")
