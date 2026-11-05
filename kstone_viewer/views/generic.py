"""Fallback card for module types the viewer has no dedicated view for."""

from .. import html
from .base import ModuleView, output_text


class GenericView(ModuleView):
    tab = "extensions"

    def build(self) -> None:
        self.card = self.ui.html(html.card(html.badge_row(self.badges(None))))
        self.add_enabled()
        self.add_home()
        self.add_ports()
        self.add_tune()
        self.add_plot()

    def update(self) -> None:
        body = html.badge_row(self.badges(self.state)) + output_text(self.declared, (self.state or {}).get("display"))
        self.set_html(self.card, html.card(body, right=html.badge(self.type, "accent")))
