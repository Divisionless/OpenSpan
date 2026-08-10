"""Contract checks for independent Spaces; every desktop operation is fake."""

import dataclasses

import spaces


def check(name, condition):
    print(("PASS " if condition else "FAIL ") + name)
    if not condition:
        raise AssertionError(name)


def two_monitors():
    model = spaces.WorkspaceModel()
    model.add_monitor("left")
    model.add_monitor("right")
    return model


# ---- every WorkspaceModelTests.cs case ---------------------------------------

model = two_monitors()
check("each monitor starts with its own workspaces and active one",
      len(model.workspaces_on("left")) == 4
      and len(model.workspaces_on("right")) == 4
      and model.active_workspace_on("left") != model.active_workspace_on("right"))

model = two_monitors()
right_before = model.active_workspace_on("right")
switched = model.switch_to_ordinal("left", 2)
check("switching one monitor leaves every other monitor alone",
      switched and model.active_workspace_on("left") == "left:2"
      and model.active_workspace_on("right") == right_before)

model = two_monitors()
check("switching to another monitor's workspace is refused",
      not model.switch_to("left", "right:1")
      and model.active_workspace_on("left") == "left:0")

model = two_monitors()
model.assign("w1", "left:0")
model.assign("w2", "left:1")
model.assign("w3", "right:0")
before = (model.is_visible("w1"), model.is_visible("w2"), model.is_visible("w3"))
model.switch_to_ordinal("left", 1)
after = (model.is_visible("w1"), model.is_visible("w2"), model.is_visible("w3"))
check("visibility follows the owning monitor's active workspace",
      before == (True, False, True) and after == (False, True, True))

model = two_monitors()
model.assign("pinned", "left:0", floating=True)
model.switch_to_ordinal("left", 3)
check("floating windows stay visible across workspaces",
      model.is_visible("pinned"))

model = two_monitors()
check("unmanaged windows are never hidden", model.is_visible("never-assigned"))

model = two_monitors()
model.assign("w1", "left:0")
model.assign("w2", "left:2")
affected = model.remove_monitor("left")
check("a detached monitor keeps its windows visible",
      len(affected) == 2 and set(affected) == {"w1", "w2"}
      and model.is_visible("w1") and model.is_visible("w2"))

model = two_monitors()
model.assign("w1", "left:0")
model.switch_to_ordinal("right", 1)
lost = model.remove_monitor("left")
moved = model.rescue_to(lost, "right")
check("rescued windows land on the survivor's active workspace",
      moved == ("w1",)
      and model.placement_of("w1").workspace_id == "right:1"
      and model.is_visible("w1"))

model = two_monitors()
model.assign("w1", "left:1")
model.switch_to_ordinal("left", 1)
model.remap_monitor("left", "left-reconnected")
check("remapping a monitor carries its workspaces and windows",
      not model.workspaces_on("left")
      and len(model.workspaces_on("left-reconnected")) == 4
      and model.active_workspace_on("left-reconnected") == "left:1"
      and model.monitor_of("left:1") == spaces.MonitorId("left-reconnected")
      and model.is_visible("w1"))

model = two_monitors()
unknown_threw = False
try:
    model.assign("w1", "nonexistent:0")
except ValueError:
    unknown_threw = True
check("assigning to an unknown workspace throws", unknown_threw)

model = two_monitors()
model.assign("w1", "left:2")
model.assign("w2", "right:0", floating=True)
model.switch_to_ordinal("left", 2)
restored = spaces.WorkspaceModel.restore(model.snapshot())
check("snapshot round-trips through restore",
      restored.active_workspace_on("left") == "left:2"
      and restored.placement_of("w1").workspace_id == "left:2"
      and restored.placement_of("w2").floating
      and restored.is_visible("w1"))

model = two_monitors()
model.assign("w1", "right:0")
snapshot = model.snapshot()
trimmed = spaces.WorkspaceSnapshot(
    tuple(item for item in snapshot.monitors
          if item.monitor == spaces.MonitorId("left")),
    snapshot.placements,
)
restored = spaces.WorkspaceModel.restore(trimmed)
check("restore drops placements whose workspace no longer exists",
      restored.placement_of("w1") is None and restored.is_visible("w1"))

model = two_monitors()
model.assign("first", "left:0")
model.assign("second", "left:0")
check("workspace windows retain assignment order",
      model.windows_on("left:0") == ("first", "second"))


# ---- pure additions required by the brief ------------------------------------

twin_a = spaces.MonitorId("shared-panel", 0)
twin_b = spaces.MonitorId("shared-panel", 1)
model = spaces.WorkspaceModel()
model.add_monitor(twin_a, 2)
model.add_monitor(twin_b, 2)
model.assign_to_active("a", twin_a)
model.assign_to_active("b", twin_b)
model.next_space(twin_a)
check("twin monitors sharing a stable key have separate space sets",
      model.active_workspace_on(twin_a) == "shared-panel:1"
      and model.active_workspace_on(twin_b) == "shared-panel#1:0"
      and model.monitor_of(model.placement_of("a").workspace_id) == twin_a
      and model.monitor_of(model.placement_of("b").workspace_id) == twin_b)

model = spaces.WorkspaceModel()
model.add_monitor("left", 2)
model.add_monitor("right", 2)
model.switch_to_ordinal("right", 1)
model.assign("moved", "left:0", floating=True)
changed = model.rehome("moved", "right")
check("a window moved between monitors re-homes to the new active space",
      changed and model.placement_of("moved") == spaces.WindowPlacement(
          "right:1", True))

low = spaces.WorkspaceModel()
high = spaces.WorkspaceModel()
low.add_monitor("low", -50)
high.add_monitor("high", 99)
check("spacesPerMonitor clamps to 1 through 16",
      len(low.workspaces_on("low")) == 1
      and len(high.workspaces_on("high")) == 16
      and spaces.clamp_spaces(True) == spaces.DEFAULT_SPACES)

model = spaces.WorkspaceModel()
model.add_monitor("panel", 3)
model.previous_space("panel")
previous_wrapped = model.active_workspace_on("panel") == "panel:2"
model.next_space("panel")
next_wrapped = model.active_workspace_on("panel") == "panel:0"
check("next and previous wrap", previous_wrapped and next_wrapped)

model.assign("one", "panel:0")
model.assign("two", "panel:1")
plan = model.visibility_plan("panel")
check("show and hide sets are disjoint",
      plan.windows_to_show == frozenset({"one"})
      and plan.windows_to_hide == frozenset({"two"})
      and plan.windows_to_show.isdisjoint(plan.windows_to_hide))


# ---- injected visibility and the five safety laws ----------------------------

class FakeApplier:
    def __init__(self, handles=(1, 2, 3)):
        self.valid = set(handles)
        self.visible = set(handles)
        self.identities = {handle: (100 + handle, "FakeWindow")
                           for handle in handles}
        self.calls = []

    def is_window(self, handle):
        return handle in self.valid

    def is_window_visible(self, handle):
        return handle in self.visible

    def identity_of(self, handle):
        return self.identities.get(handle)

    def show_window(self, handle, command):
        self.calls.append((handle, command))
        if handle not in self.valid:
            return False
        if command == spaces.SW_HIDE:
            was_visible = handle in self.visible
            self.visible.discard(handle)
            return was_visible
        self.visible.add(handle)
        return True


fake = FakeApplier()
visibility = spaces.WindowVisibilityController(fake)
identity = fake.identity_of(1)
hidden = visibility.hide(1, identity)
check("every hide records enough identity to undo it",
      hidden and visibility.hidden == (spaces.HiddenWindow(1, identity),)
      and 1 not in fake.visible)

visibility.hide(2, fake.identity_of(2))
first_restore = visibility.restore_all()
calls_after_first = tuple(fake.calls)
second_restore = visibility.restore_all()
check("restore_all shows every owned window and is idempotent",
      first_restore == 2 and second_restore == 0
      and fake.visible == {1, 2, 3}
      and tuple(fake.calls) == calls_after_first)

fake = FakeApplier((10, 11))
visibility = spaces.WindowVisibilityController(fake)
module = spaces.SpacesModule(visibility_factory=lambda: visibility)
left = spaces.MonitorId("left")
right = spaces.MonitorId("right")
windows = (
    spaces.DesktopWindow(10, left, fake.identity_of(10), "left window"),
    spaces.DesktopWindow(11, right, fake.identity_of(11), "right window"),
)
module.enable((left, right), windows, spaces_per_monitor=2)
module.next_space(left)
was_hidden = 10 not in fake.visible and visibility.is_hidden_by_us(10)
module.topology_changed((right,))
check("a disappearing monitor restores its windows",
      was_hidden and 10 in fake.visible and not visibility.hidden
      and module.model.monitor_of(
          module.model.placement_of("A").workspace_id) == right)
module.disable()

factory_calls = []
fake = FakeApplier((20,))
module = spaces.SpacesModule(
    visibility_factory=lambda: factory_calls.append("constructed")
    or spaces.WindowVisibilityController(fake))
check("construction and import hide nothing before explicit enable",
      not module.enabled and not factory_calls and not fake.calls)
module.enable((left,), (spaces.DesktopWindow(
    20, left, fake.identity_of(20), "existing"),), spaces_per_monitor=2)
check("enable assigns existing windows to active space without hiding",
      module.enabled and factory_calls == ["constructed"] and not fake.calls)
module.disable()

fake = FakeApplier((30,))
visibility = spaces.WindowVisibilityController(fake)
module = spaces.SpacesModule(visibility_factory=lambda: visibility)
module.enable((left,), (spaces.DesktopWindow(
    30, left, fake.identity_of(30), "owned"),), spaces_per_monitor=2)
module.next_space(left)
release_observations = []
module.add_release(lambda: release_observations.append(
    (30 in fake.visible, not visibility.hidden)))
module.disable()
check("disable restores everything before releasing anything",
      release_observations == [(True, True)]
      and 30 in fake.visible and not module.enabled and module.visibility is None)

check("commands are declarations only and the feature ships disabled",
      tuple(item.id for item in spaces.COMMANDS)
      == (spaces.NEXT_SPACE_COMMAND, spaces.PREVIOUS_SPACE_COMMAND)
      and spaces.FEATURE_DECLARATION.default_shortcuts
      == {spaces.NEXT_SPACE_COMMAND: (), spaces.PREVIOUS_SPACE_COMMAND: ()}
      and not spaces.FEATURE_DECLARATION.default_enabled)

# ---- confinement: no window may span two displays ----------------------------
# Doug, 2026-08-10: macOS forbids a window spanning screens while Displays
# Have Separate Spaces is on, and that is what makes ownership unambiguous.
# Geometry is checked on NEGATIVE-origin displays -- his left panel is at
# x=-1920 and his top panel at y=-1080 -- because a formula verified only on
# the primary is not verified.
from window_tracker import PixelRect
from spaces import confine_to_work_area, straddles
LEFT = PixelRect(-1920, 0, 1920, 1080)     # his non-primary panel
PRIMARY = PixelRect(0, 0, 1920, 1080)

inside = PixelRect(-1800, 100, 800, 600)
check("a window fully on its display is left alone",
      not straddles(inside, LEFT)
      and confine_to_work_area(inside, LEFT) == inside)

spanning = PixelRect(-200, 100, 800, 600)   # half on LEFT, half on PRIMARY
check("a window across the boundary is detected",
      straddles(spanning, LEFT))
pulled = confine_to_work_area(spanning, LEFT)
check("it is pulled fully onto the owning display, size kept",
      not straddles(pulled, LEFT)
      and pulled.width == 800 and pulled.height == 600
      and pulled.x == -1920 + 1920 - 800)

oversized = PixelRect(-1900, 50, 3000, 1200)
shrunk = confine_to_work_area(oversized, LEFT)
check("a window larger than the display shrinks to fit, not hangs off",
      shrunk.width == 1920 and shrunk.height == 1080
      and not straddles(shrunk, LEFT))

maximized = PixelRect(-1920, 0, 1920, 1080)
check("a maximized window is not treated as straddling",
      not straddles(maximized, LEFT)
      and confine_to_work_area(maximized, LEFT) == maximized)

check("confinement is idempotent",
      confine_to_work_area(pulled, LEFT) == pulled)

above = PixelRect(4, -1080, 1920, 1080)     # his third panel, negative Y
hangs_up = PixelRect(100, -1200, 600, 400)
check("negative-Y displays confine on the Y axis too",
      straddles(hangs_up, above)
      and not straddles(confine_to_work_area(hangs_up, above), above))
