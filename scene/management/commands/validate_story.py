"""Validate a story's configuration before spending money on it.

    manage.py validate_story                    # every story
    manage.py validate_story --story "My Book"  # one, by name
    manage.py validate_story --story 1          # or by id
    manage.py validate_story --strict           # warnings count as failures too

Exits non-zero when anything failed, so it can gate a render in a script or in CI. This is the
form the checks are most useful in: the admin action tells you after you have logged in and gone
looking, whereas this can run immediately before the spend.
"""
from django.core.management.base import BaseCommand, CommandError

from scene.doctor import FAIL, check_story, summarise
from scene.models import Story


class Command(BaseCommand):
    help = "Check a story's configuration for the things that silently waste generation spend."

    def add_arguments(self, parser):
        parser.add_argument("--story", help="Story name or id. Omit to check every story.")
        parser.add_argument("--strict", action="store_true",
                            help="Treat warnings as failures in the exit code.")

    def handle(self, *args, **options):
        stories = Story.objects.all().order_by("id")
        wanted = options.get("story")
        if wanted:
            stories = stories.filter(pk=wanted) if str(wanted).isdigit() else stories.filter(name=wanted)
            if not stories.exists():
                raise CommandError(f"No story matching {wanted!r}.")

        total_fail = total_warn = 0
        for story in stories:
            findings = check_story(story)
            fails, warns, verdict = summarise(findings)
            total_fail += fails
            total_warn += warns

            self.stdout.write(f"\n{story.name}  (id {story.pk}, render type {story.render_type})")
            for finding in findings:
                style = self.style.ERROR if finding.severity == FAIL else self.style.WARNING
                self.stdout.write(style(f"  {finding}"))
            style = self.style.ERROR if fails else (self.style.WARNING if warns else self.style.SUCCESS)
            self.stdout.write(style(f"  {verdict}"))

        if total_fail or (options["strict"] and total_warn):
            raise CommandError(
                f"{total_fail} failure(s), {total_warn} warning(s). Do not spend.")
