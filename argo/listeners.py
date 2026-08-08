# nautilus_app/position_listener.py
import django
import os

# Initialize Django ORM context if running as a standalone Nautilus script
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "project.settings_argo")
django.setup()

from django.utils import timezone
from nautilus_trader.common.actor import Actor
from nautilus_trader.execution.messages import PositionChanged, PositionClosed
from argo.models import Position as DjangoPosition


class DjangoPositionLogger(Actor):
    # ... setup code ...

    def on_position_changed(self, event: PositionChanged):
        pos = event.position
        
        # Determine directional side
        side = "LONG" if pos.signed_qty > 0 else "SHORT"

        DjangoPosition.objects.update_or_create(
            account_id=str(pos.account_id),
            instrument_id=str(pos.instrument_id),
            status=DjangoPosition.Status.OPEN, # Keep record active
            defaults={
                "side": side,
                "quantity": abs(float(pos.quantity)),
                "avg_price": float(pos.avg_price or 0.0),
                "unrealized_pnl": float(pos.unrealized_pnl or 0.0),
                "realized_pnl": float(pos.realized_pnl or 0.0),
            }
        )

    def on_position_closed(self, event: PositionClosed):
        pos = event.position
        
        # Mark as CLOSED in Django, keep quantity=0 and preserve final realized PnL
        DjangoPosition.objects.filter(
            account_id=str(pos.account_id),
            instrument_id=str(pos.instrument_id),
            status=DjangoPosition.Status.OPEN,
        ).update(
            status=DjangoPosition.Status.CLOSED,
            quantity=0,
            unrealized_pnl=0.0,
            realized_pnl=float(pos.realized_pnl or 0.0),
        )