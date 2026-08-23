from charlie.browser import intent, recipes, session
from charlie.browser.intent import Constraint
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

    def evaluate(self, script):
        return {
            "type": self.attributes.get("type", ""),
            "labels": self.attributes.get("label_semantics", ""),
            "form": self.attributes.get("form_semantics", ""),
            "submit": self.attributes.get("submit_semantics", ""),
        }


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


def _page_shape(search_role, filter_role, sort_role, *, filter_text="16 GB RAM", filter_label=None):
    return _SemanticPage(
        [
            _Control(search_role, "Search products", placeholder="Search products"),
            _Control("link", "Alpha Laptop 16 GB RAM Display Type OLED ₹79,990", "/item/alpha"),
            _Control("link", "Beta Laptop 16 GB RAM Display Type OLED ₹69,990", "/item/beta"),
            _Control(filter_role, filter_text, aria_label=filter_label or filter_text),
            _Control(sort_role, "Price Low to High", aria_label="Price Low to High"),
        ]
    )


def test_semantic_controls_and_results_survive_four_dom_shapes():
    session.reset_session()
    for page in (
        _page_shape("searchbox", "checkbox", "button"),
        _SemanticPage(
            [
                _Control("textbox", "Search products", placeholder="Search products"),
                _Control("link", "Alpha Laptop 16 GB RAM Display Type OLED ₹79,990", "/item/alpha"),
                _Control("link", "Beta Laptop 16 GB RAM Display Type OLED ₹69,990", "/item/beta"),
                _Control("button", "Display Type", aria_label="Display Type"),
                _Control("listbox", "OLED", aria_label="Display Type"),
                _Control("option", "OLED", aria_label="OLED"),
                _Control("menuitem", "Price Low to High", aria_label="Price Low to High"),
            ]
        ),
        _page_shape("searchbox", "radio", "button", filter_text="OLED", filter_label="Display Type OLED"),
        _SemanticPage(
            [
                _Control("combobox", "Search products", placeholder="Search products"),
                _Control("link", "Alpha Laptop 16 GB RAM Display Type OLED ₹79,990", "/item/alpha"),
                _Control("link", "Beta Laptop 16 GB RAM Display Type OLED ₹69,990", "/item/beta"),
                _Control("combobox", "Display Type", aria_label="Display Type"),
                _Control("option", "OLED", aria_label="OLED"),
                _Control("combobox", "Price Low to High", aria_label="Price Low to High"),
            ]
        ),
    ):
        assert recipes.discover_search_control(page) is not None
        results = recipes.discover_results(page, require_price=True)
        assert [item["title"] for item in results] == [
            "Alpha Laptop 16 GB RAM Display Type OLED ₹79,990",
            "Beta Laptop 16 GB RAM Display Type OLED ₹69,990",
        ]
        has_display_control = any(
            control.role != "link"
            and (
                "display type" in control.text.casefold()
                or "display type" in str(control.attributes.get("aria-label", "")).casefold()
            )
            for control in page.controls
        )
        requested = Constraint("display type", "eq", "OLED") if has_display_control else Constraint(
            "ram", "eq", "16 GB"
        )
        assert recipes.apply_constraint(page, requested) is True
        assert recipes.apply_sort(page, "price", "ascending") is True
        verified, detail = recipes.verify_constraints(
            results,
            [requested, Constraint("price", "lte", "80000")],
        )
        assert verified, detail


def test_intent_slots_and_http_resolution_are_environment_driven():
    parsed = intent.parse_browser_intent("Filter these results under ₹80,000 for 16 GB RAM.", "shop.example")
    assert parsed.operation == "FILTER"
    assert parsed.attribute == "ram"
    assert parsed.value == "16 GB"
    assert parsed.operator == "lte"

    examples = {
        "16 GB RAM under ₹80,000": {("ram", "eq", "16 GB"), ("price", "lte", "₹ 80000")},
        "at least 8 GB RAM and 512 GB storage": {("ram", "gte", "8 GB"), ("storage", "eq", "512 GB")},
        "under $1000 with rating above 4": {("price", "lte", "$ 1000"), ("rating", "gte", "4")},
        "brand Lenovo and minimum 16 GB memory": {("brand", "eq", "Lenovo"), ("ram", "gte", "16 GB")},
        "Display Type OLED": {("display type", "eq", "OLED")},
    }
    for sample, expected in examples.items():
        actual = {
            (item.attribute, item.operator, item.value)
            for item in intent.parse_browser_intent(sample).constraints
        }
        assert actual == expected

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


def test_search_discovery_requires_positive_semantic_evidence():
    searchbox = _Control("searchbox", "")
    placeholder = _Control("textbox", "", placeholder="Search products")
    form_search = _Control("textbox", "", form_semantics="Catalog", submit_semantics="Search")
    email = _Control("textbox", "", aria_label="Email", type="email")
    password = _Control("textbox", "", aria_label="Password", type="password")

    assert recipes.discover_search_control(_SemanticPage([searchbox])) is searchbox
    assert recipes.discover_search_control(_SemanticPage([placeholder])) is placeholder
    assert recipes.discover_search_control(_SemanticPage([form_search])) is form_search
    assert recipes.discover_search_control(_SemanticPage([email, password, _Control("button", "Login")])) is None


def test_price_constraints_accept_semantic_shapes_and_reject_ambiguous_selectors():
    labelled_min = _PriceSelect(["Minimum", "₹20,000", "₹40,000"], "Minimum")
    labelled_max = _PriceSelect(["Maximum", "₹40,000", "₹60,000"], "Maximum")
    assert recipes.apply_constraint(_SemanticPage([labelled_min, labelled_max]), "price", "lte", "₹50,000")
    assert labelled_max.selected_label == "₹40,000"

    from_select = _PriceSelect(["From", "₹20,000", "₹40,000"], "From")
    to_select = _PriceSelect(["To", "₹40,000", "₹60,000"], "To")
    assert recipes.apply_constraint(_SemanticPage([from_select, to_select]), "price", "gte", "₹30,000")
    assert from_select.selected_label == "₹40,000"

    only_max = _PriceSelect(["Maximum price", "₹20,000", "₹50,000"], "Maximum price")
    assert recipes.apply_constraint(_SemanticPage([only_max]), "price", "lte", "₹50,000")
    assert only_max.selected_label == "₹50,000"

    ambiguous_a = _PriceSelect(["₹10,000", "₹20,000", "₹30,000"], "₹10,000")
    ambiguous_b = _PriceSelect(["₹40,000", "₹50,000", "₹60,000"], "₹40,000")
    assert not recipes.apply_constraint(
        _SemanticPage([ambiguous_a, ambiguous_b]), "price", "lte", "₹50,000"
    )
    assert ambiguous_a.selected_label == "₹10,000"
    assert ambiguous_b.selected_label == "₹40,000"


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


def test_repository_ref_accepts_rendered_nested_branch_control():
    class _Links:
        def evaluate_all(self, script, limit):
            return []

    class _ControlPage:
        url = "https://github.com/acme/widget"

        def locator(self, selector):
            assert selector == "a[href]"
            return _Links()

        def get_by_role(self, role, **kwargs):
            if role == "button":
                return _Locator([_Control("button", "feature/browser/dynamic-runtime")])
            return _Locator([])

    assert recipes.github_repository_ref(_ControlPage()) == "feature/browser/dynamic-runtime"
