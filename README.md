# Cook Manager

Run cooks in Home Assistant on any temperature probe you already have. Pick a probe, pick a recipe
or a target, press start. Cook Manager tracks the cook, estimates the time remaining, moves through
recipe stages and sends alerts. Everything runs locally; no vendor cloud or app is involved.

It uses temperature sensors that other integrations provide (Inkbird, Meater, ESPHome, anything
that is a temperature sensor), so probes from different brands can be mixed in one cook. One
device it talks to itself: the Kamado Joe iKamand, whose cloud service has been shut down
(see [iKamand](#ikamand)).

**Status: early (0.2.0).** Tested against Home Assistant 2026.10.

## What it does

- **Cook slots.** A fixed number of slots (default 4), each one a cook that can run at the same time.
- **Probe pool.** You choose which temperature sensors are probes. A slot offers the ones that
  have a reading right now.
- **Meat and grill probes.** Each slot takes a meat probe, and optionally a grill probe.
- **Recipes.** Built-in recipes with doneness levels, carryover, rest time and stages (for example
  smoke, wrap, rest). Add your own as YAML files.
- **Time remaining.** Fitted to the probe curve. With a grill probe the meat is modelled as heating
  towards the grill temperature; without one the recent rise is extrapolated. During a
  low-and-slow stall no estimate is shown.
- **Alerts.** Approaching target, target reached, stage changes, rest finished, grill above or
  below its band, probe lost, stall.
- **Grill controller (optional).** A built-in iKamand, or a climate entity from another
  integration, is handed the grill target at each stage. The controller keeps doing its own
  regulation; Cook Manager only sets the target.
- **Survives restarts.** A running cook carries on after Home Assistant restarts.

## Install

Add this repository as a custom repository in the Home Assistant Marketplace (HACS on versions
before 2026.11), install **Cook Manager**, restart, then add it under
**Settings → Devices & services → Add integration**.

Manual install: copy `custom_components/cook_manager` into your `config/custom_components` folder
and restart.

## Setup options

| Option | Meaning |
|---|---|
| Cook slots | How many cooks can run at once (1 to 8). |
| Probes | Temperature sensors to offer as probes. A climate entity is read through its current temperature. |
| Grill controllers | Climate entities from other integrations that may be handed the grill target. |
| iKamand address | IP address of an iKamand for Cook Manager to talk to directly. Optional. |
| Notify | Notify services that receive alerts. |
| Readings that mean no probe | Values a probe reports with nothing plugged in, for example `-1`. |
| Approaching alert | Degrees before a meat target at which to alert. |
| Grill band | How far the grill may sit from its target before an alert. |

## Entities

For each slot (`Cook 1`, `Cook 2`, …):

| Entity | Purpose |
|---|---|
| `sensor.cook_1` | Status: idle, cooking, resting, done. Attributes carry the detail. |
| `sensor.cook_1_temperature`, `sensor.cook_1_grill_temperature` | The chosen probes. |
| `sensor.cook_1_time_remaining`, `sensor.cook_1_finish_time` | To the pull temperature while cooking, rest left while resting. |
| `sensor.cook_1_progress`, `sensor.cook_1_stage` | Progress to the pull temperature, current stage. |
| `binary_sensor.cook_1_attention` | On when done, the probe is lost or the grill is out of its band. |
| `select.cook_1_probe`, `select.cook_1_grill_probe`, `select.cook_1_grill_controller` | What the slot uses. |
| `select.cook_1_recipe`, `select.cook_1_doneness` | Recipe and doneness; choosing them fills in the targets. |
| `number.cook_1_target`, `number.cook_1_grill_target` | Targets; can be changed during a cook. |
| `number.cook_1_weight` | Weight of the meat, optional; stored with the cook record. |
| `text.cook_1_name` | Name used in alerts. |
| `button.cook_1_start`, `button.cook_1_stop`, `button.cook_1_next_stage` | Controls. |

Shared: `binary_sensor.cook_manager_cooking` (any cook running),
`sensor.cook_manager_active_cooks`, `sensor.cook_manager_probes_online`.

## iKamand

Enter the iKamand's IP address in the setup options and Cook Manager talks to it directly over
your local network. No cloud, no app and no other integration is needed. Give the iKamand a fixed
address in your router. If you used another iKamand integration, remove it first; two programs
commanding one iKamand will fight.

It adds an `iKamand` device:

| Entity | Purpose |
|---|---|
| `climate.ikamand` | Thermostat. Heat holds the pit target with the fan; off stops the fan. |
| `sensor.ikamand_pit_temperature`, `sensor.ikamand_probe_1` to `_3` | Pit and food probes. Unknown while nothing is plugged in. |
| `sensor.ikamand_fan` | Fan output in percent. |
| `switch.ikamand_fire_it_up`, `number.ikamand_fire_it_up_duration` | Fan flat out for the set minutes to light the charcoal. |

The pit and food probes appear in every slot's probe list and the iKamand appears as a grill
controller. With the iKamand as a slot's grill controller:

- Starting the cook starts the iKamand on the grill target, and each recipe stage hands it the
  next one.
- When the meat comes off (the rest starts, or the cook is stopped) the fan is stopped, unless
  another running cook uses the same iKamand.
- After Fire it up, it goes back to the cook's grill target instead of switching off.
- Cook Manager keeps the iKamand on its target: after a power blip or a dropped connection the
  target is sent again, and the 24 hour limit the iKamand puts on a cook is renewed, so long
  cooks do not end by themselves.
- If Home Assistant restarts while the iKamand is running, the iKamand carries on.

The iKamand still does the fan regulation itself. Its range is 66 to 260 °C.

The local protocol was worked out by the
[plmilord iKamand integration](https://github.com/plmilord/Hass.io-custom-component-ikamand).

## Services

- `cook_manager.start_cook`: slot, plus any of name, probe, grill_probe, grill_controller,
  recipe, doneness, target, grill_target, weight. Probes and the controller are entity ids;
  the iKamand's own entities work here too.
- `cook_manager.stop_cook`, `cook_manager.next_stage`: slot.
- `cook_manager.reload_recipes`

## Events

Every alert is also fired as a `cook_manager_event` event with `slot`, `name`, `type` and
`message`, for your own automations. Types: `started`, `stage_started`, `approaching`,
`target_reached`, `done`, `stopped`, `grill_ready`, `grill_low`, `grill_high`,
`grill_recovered`, `probe_lost`, `probe_restored`, `stalled`.

## Your own recipes

Put YAML files in `config/cook_manager/recipes/` and call `cook_manager.reload_recipes`.
Temperatures are degrees Celsius.

```yaml
id: my_brisket
name: My brisket
category: beef
safe_min: 63
doneness:
  - {id: sliced, name: Sliced, target: 93}
  - {id: pulled, name: Pulled, target: 96}
stages:
  - id: smoke
    name: Smoke
    grill_temp: 120
    until: {meat_temp: 70}
    notify: Wrap the brisket
  - id: wrapped
    name: Wrapped
    grill_temp: 135
    until: {meat_temp: pull}
  - id: rest
    name: Rest
    rest: true
    until: {minutes: 60}
```

A stage ends on exactly one of `meat_temp` (a number, `target`, or `pull` for target minus
carryover), `grill_temp`, `minutes`, or `manual: true` (waits for Next stage). A recipe without
stages cooks to the pull temperature and then rests for `rest_minutes`.

## Cook records

Every finished cook is written to its own JSON file in `config/cook_manager/cooks/`, for example
`20261121-1830_brisket.json`. The file holds the recipe, targets, weight, stages, every alert with
its time, and the full curve: one row every 30 seconds of `[time, meat, grill]` in degrees Celsius,
including the rest. Nothing else is needed: no InfluxDB, and the files are not affected by the
Home Assistant recorder purge. They are plain files, so they can be backed up, graphed or shared.

Set `number.cook_1_weight` before starting if you know it; weight is stored with the record.

## Food safety

The built-in recipes carry the USDA FSIS safe minimum internal temperature as `safe_min`. Some
doneness levels sit below it by choice (a rare steak). Cook Manager reports temperatures; what is
safe to eat is your call. Check with a calibrated thermometer.

## Development

```
pip install -r requirements_test.txt
pytest
```

The cook logic lives in `custom_components/cook_manager/engine/` and has no Home Assistant
imports.
