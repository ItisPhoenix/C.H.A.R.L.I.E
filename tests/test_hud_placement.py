from charlie.hud import placement


def test_center_region_centers_in_screen():
    screen = (0, 0, 1920, 1080)
    rect = placement.region_to_rect("center", screen, "workspace")
    w, h = placement.surface_size("workspace")
    x, y, rw, rh = rect
    assert (rw, rh) == (w, h)
    assert x == (1920 - w) // 2
    assert y == (1080 - h) // 2


def test_top_right_hugs_top_right_corner_with_margin():
    screen = (0, 0, 1920, 1080)
    x, y, w, h = placement.region_to_rect("top_right", screen, "widget")
    assert x + w == 1920 - placement._MARGIN
    assert y == placement._MARGIN


def test_bottom_left_hugs_bottom_left_corner_with_margin():
    screen = (0, 0, 1920, 1080)
    x, y, w, h = placement.region_to_rect("bottom_left", screen, "widget")
    assert x == placement._MARGIN
    assert y + h == 1080 - placement._MARGIN


def test_secondary_monitor_offset_screen_respected():
    screen = (1920, 0, 1920, 1080)
    x, y, w, h = placement.region_to_rect("top_left", screen, "widget")
    assert x == 1920 + placement._MARGIN
    assert y == placement._MARGIN


def test_stack_index_offsets_within_same_region():
    screen = (0, 0, 1920, 1080)
    _, y0, _, h = placement.region_to_rect("top_right", screen, "widget", stack_index=0)
    _, y1, _, _ = placement.region_to_rect("top_right", screen, "widget", stack_index=1)
    assert y1 == y0 + h + placement._MARGIN


def test_scale_grows_surface_size():
    w1, h1 = placement.surface_size("modal", scale=1.0)
    w2, h2 = placement.surface_size("modal", scale=1.5)
    assert w2 == int(w1 * 1.5)
    assert h2 == int(h1 * 1.5)


def test_unknown_mode_falls_back_to_widget_size():
    assert placement.surface_size("mystery") == placement.surface_size("widget")
