from charlie.browser import intent, recipes, session
from charlie.known_apps import resolve_website_url


class _Control:
    def __init__(self, role, text, href="", **attributes):
        self.role = role
        self.text = text
        self.href = href
        self.attributes = attributes
        self.clicked = False

    def is_visible(self):
        return True

    def get_attribute(self, name):
        if name == "href":
            return self.href
        return self.attributes.get(name)

    def inner_text(self, timeout=None):
        return self.text

    def click(self, timeout=None):
        self.clicked = True

    def fill(self, value, timeout=None):
        self.text = value

    def press(self, key):
        return None


class _Locator:
    def __init__(self, items):
        self.items = items

    def count(self):
        return len(self.items)

    def nth(self, index):
        return self.items[index]


class _Option(_Control):
    def __init__(self, text, selected=False):
        super().__init__("option", text)
        self.selected = selected

    def get_attribute(self, name):
        if name == "selected" and self.selected:
            return "selected"
        return super().get_attribute(name)


class _PriceSelect(_Control):
    def __init__(self, labels, selected):
        super().__init__("combobox", " ".join(labels))
        self.options = [_Option(label, label == selected) for label in labels]
        self.selected_label = selected

    def get_by_role(self, role, **kwargs):
        return _Locator(self.options if role == "option" else [])

    def locator(self, selector):
        if selector == "option:checked":
            return _Locator([option for option in self.options if option.selected])
        return _Locator([])

    def select_option(self, label):
        self.selected_label = label
        for option in self.options:
            option.selected = option.text == label


class _SemanticPage:
    def __init__(self, controls, url="https://shop.example/search?q=laptops"):
        self.controls = controls
        self.url = url

    def get_by_role(self, role, **kwargs):
        return _Locator([control for control in self.controls if control.role == role])


def _page_shape(search_role, filter_role, sort_role):
    return _SemanticPage(
        [
            _Control(search_role, "Search products", placeholder="Search products"),
            _Control("link", "Alpha Laptop 16 GB RAM ₹79,990", "/item/alpha"),
            _Control("link", "Beta Laptop 16 GB RAM ₹69,990", "/item/beta"),
            _Control(filter_role, "16 GB RAM", aria_label="16 GB RAM"),
            _Control(sort_role, "Price Low to High", aria_label="Price Low to High"),
        ]
    )


def test_semantic_controls_and_results_survive_two_dom_shapes():
    session.reset_session()
    for page in (
        _page_shape("searchbox", "checkbox", "button"),
        _page_shape("textbox", "option", "menuitem"),
    ):
        assert recipes.discover_search_control(page) is not None
        results = recipes.discover_results(page, require_price=True)
        assert [item["title"] for item in results] == [
            "Alpha Laptop 16 GB RAM ₹79,990",
            "Beta Laptop 16 GB RAM ₹69,990",
        ]
        assert recipes.apply_constraint(page, "ram", "eq", "16 GB") is True
        assert recipes.apply_sort(page, "price", "ascending") is True
        verified, detail = recipes.verify_constraints(
            results,
            {"ram": "16 GB", "price": {"operator": "lte", "value": 80000}},
        )
        assert verified, detail


def test_intent_slots_and_http_resolution_are_environment_driven():
    parsed = intent.parse_browser_intent("Filter these results under ₹80,000 for 16 GB RAM.", "shop.example")
    assert parsed.operation == "FILTER"
    assert parsed.attribute == "ram"
    assert parsed.value == "16 GB"
    assert parsed.operator == "lte"

    assert intent.parse_browser_intent("Sort these results by price low to high.", "shop.example").operation == "SORT"
    assert resolve_website_url("https://internal-service/catalog") == "https://internal-service/catalog"
    assert resolve_website_url("https://example.invalid/path") == "https://example.invalid/path"
    assert resolve_website_url("my desktop") is None


def test_price_constraints_choose_min_or_max_control_from_rendered_evidence():
    minimum = _PriceSelect(["Min", "₹20,000", "₹40,000", "₹75,000"], "Min")
    maximum = _PriceSelect(["₹20,000", "₹40,000", "₹75,000", "₹75,000+"], "₹75,000+")
    page = _SemanticPage([minimum, maximum])

    assert recipes.apply_constraint(page, "price", "lte", "₹75,000") is True
    assert maximum.selected_label == "₹75,000"
    assert minimum.selected_label == "Min"

    assert recipes.apply_constraint(page, "price", "gte", "₹40,000") is True
    assert minimum.selected_label == "₹40,000"


def test_repository_ref_is_discovered_from_current_page_links():
    class _Links:
        def evaluate_all(self, script, limit):
            return [
                {"href": "/acme/widget/tree/release", "name": "release"},
                {"href": "/acme/widget/tree/release/src", "name": "src"},
            ][:limit]

    class _RepositoryPage:
        url = "https://github.com/acme/widget"

        def locator(self, selector):
            assert selector == "a[href]"
            return _Links()

    assert recipes.github_repository_ref(_RepositoryPage()) == "release"
    assert recipes._github_ref_from_url("https://github.com/acme/widget/tree/feature%2Fui") == "feature/ui"
