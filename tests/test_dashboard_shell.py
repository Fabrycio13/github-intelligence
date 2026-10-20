"""Public HTML shell contract; browser interactions are smoke-tested separately."""
from html.parser import HTMLParser

from test_dashboard import running


class Shell(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.elements = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def test_shell_has_separate_navigable_pages_and_modal_without_writes():
    with running() as (_, client, writes):
        response = client.get("/")
        shell = Shell(response.text)
        links = {attrs.get("href") for tag, attrs in shell.elements if tag == "a"}
        assert {"#/dashboard", "#/repositorios", "#/analises"} <= links
        pages = {attrs.get("data-page") for _, attrs in shell.elements if "data-page" in attrs}
        assert pages == {"dashboard", "repositorios", "analises", "relatorio"}
        dialog = next(attrs for tag, attrs in shell.elements if tag == "dialog")
        assert dialog["id"] == "onboarding"
        assert dialog["aria-labelledby"] == "onboarding-title"
        assert any(attrs.get("id") == "close-onboarding" for _, attrs in shell.elements)
        assert writes == []


def test_shell_omits_permanent_notices_but_keeps_accessible_operation_feedback():
    with running() as (_, client, writes):
        response = client.get("/")
        shell = Shell(response.text)
        assert not any(attrs.get("id") == "connection-status" for _, attrs in shell.elements)
        assert not any("notice" in attrs.get("class", "").split() for _, attrs in shell.elements)
        assert "Supabase TLS" not in response.text
        message = next(attrs for _, attrs in shell.elements if attrs.get("id") == "message")
        assert "hidden" in message
        assert message["role"] == "status" and message["aria-live"] == "polite"
        assert writes == []


def test_sidebar_uses_outline_icons_without_changing_existing_routes_or_brand():
    with running() as (_, client, writes):
        shell = Shell(client.get("/").text)
        icons = [attrs for tag, attrs in shell.elements
                 if tag == "svg" and "nav-icon" in attrs.get("class", "").split()]
        assert len(icons) == 3
        assert all(icon.get("aria-hidden") == "true" and icon.get("fill") == "none" for icon in icons)
        routes = {attrs.get("href") for _, attrs in shell.elements if "data-nav" in attrs}
        assert routes == {"#/dashboard", "#/repositorios", "#/analises"}
        assert any(tag == "img" and attrs.get("src") == "/logo.png" for tag, attrs in shell.elements)
        assert writes == []


def test_sidebar_has_accessible_collapse_control_and_named_icon_links():
    with running() as (_, client, writes):
        shell = Shell(client.get("/").text)
        control = next(attrs for tag, attrs in shell.elements
                       if tag == "button" and attrs.get("id") == "sidebar-toggle")
        assert control["type"] == "button"
        assert control["aria-controls"] == "sidebar-navigation"
        assert control["aria-expanded"] == "true"
        assert control["aria-label"] == "Recolher menu lateral"
        assert any(tag == "nav" and attrs.get("id") == "sidebar-navigation"
                   for tag, attrs in shell.elements)
        names = {attrs.get("aria-label") for _, attrs in shell.elements if "data-nav" in attrs}
        assert names == {"Dashboard", "Repositórios", "Análises"}
        assert writes == []


def test_shell_serves_brand_logo_and_icon():
    with running() as (_, client, writes):
        logo_res = client.get("/logo.png")
        assert logo_res.status_code == 200
        assert logo_res.headers["content-type"] == "image/png"
        assert len(logo_res.content) > 1000

        icon_res = client.get("/logo-icon.png")
        assert icon_res.status_code == 200
        assert icon_res.headers["content-type"] == "image/png"
        assert len(icon_res.content) > 1000

        res = client.get("/")
        assert 'src="/logo.png"' in res.text
        assert writes == []
