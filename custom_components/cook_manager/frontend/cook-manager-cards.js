/* Cook Manager dashboard cards.
 *
 *   type: custom:cook-manager-cook-card      slot: 1
 *   type: custom:cook-manager-ikamand-card
 *   type: custom:cook-manager-summary-card   navigation_path: /lovelace/bbq
 *
 * Served by the integration itself; nothing to install. The cards find their entities through
 * the device they belong to, so renamed entity ids keep working.
 */
const DOMAIN = "cook_manager";

const SLOT_DEFAULTS = (n) => ({
  status: `sensor.cook_${n}`,
  temperature: `sensor.cook_${n}_temperature`,
  grill_temperature: `sensor.cook_${n}_grill_temperature`,
  time_remaining: `sensor.cook_${n}_time_remaining`,
  finish_time: `sensor.cook_${n}_finish_time`,
  progress: `sensor.cook_${n}_progress`,
  attention: `binary_sensor.cook_${n}_attention`,
  probe: `select.cook_${n}_probe`,
  grill_probe: `select.cook_${n}_grill_probe`,
  grill_controller: `select.cook_${n}_grill_controller`,
  recipe: `select.cook_${n}_recipe`,
  doneness: `select.cook_${n}_doneness`,
  target: `number.cook_${n}_target`,
  grill_target: `number.cook_${n}_grill_target`,
  weight: `number.cook_${n}_weight`,
  name: `text.cook_${n}_name`,
  start: `button.cook_${n}_start`,
  stop: `button.cook_${n}_stop`,
  next_stage: `button.cook_${n}_next_stage`,
});

const IKAMAND_DEFAULTS = {
  ikamand_climate: "climate.ikamand",
  ikamand_pit: "sensor.ikamand_pit_temperature",
  ikamand_p1: "sensor.ikamand_probe_1",
  ikamand_p2: "sensor.ikamand_probe_2",
  ikamand_p3: "sensor.ikamand_probe_3",
  ikamand_fan: "sensor.ikamand_fan",
  ikamand_boost: "switch.ikamand_fire_it_up",
  ikamand_boost_minutes: "number.ikamand_fire_it_up_duration",
};

/* Entities of the Cook Manager device whose identifier passes `match`, by translation key. */
function deviceEntities(hass, match, defaults) {
  const found = { ...defaults };
  const device = Object.values(hass.devices || {}).find((dev) =>
    (dev.identifiers || []).some((pair) => pair[0] === DOMAIN && match(String(pair[1])))
  );
  if (!device) return found;
  for (const entry of Object.values(hass.entities || {})) {
    if (entry.device_id !== device.id) continue;
    const key = entry.translation_key || (entry.entity_id.startsWith("climate.") ? "ikamand_climate" : null);
    if (key && key in found) found[key] = entry.entity_id;
  }
  return found;
}

const esc = (text) =>
  String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const num = (value) => {
  const parsed = parseFloat(value);
  return Number.isFinite(parsed) ? parsed : null;
};
const deg = (value) => (value === null || value === undefined ? "--" : `${Math.round(value)}°`);
const duration = (minutes) => {
  if (minutes === null) return "--";
  const m = Math.max(0, Math.round(minutes));
  return m >= 60 ? `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")}` : `${m} min`;
};
const clock = (iso) => {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
};

/* A 270 degree dial. `fraction` 0..1 fills it. */
function dial(fraction, inner, tone) {
  const r = 84;
  const c = 2 * Math.PI * r;
  const arc = c * 0.75;
  const fill = arc * Math.max(0, Math.min(1, fraction || 0));
  return `
    <div class="dial ${tone || ""}">
      <svg viewBox="0 0 200 200" aria-hidden="true">
        <defs>
          <linearGradient id="heat" x1="0" y1="1" x2="1" y2="0">
            <stop offset="0" stop-color="var(--cm-heat-1)"/><stop offset="1" stop-color="var(--cm-heat-2)"/>
          </linearGradient>
        </defs>
        <g transform="rotate(135 100 100)" fill="none" stroke-linecap="round" stroke-width="13">
          <circle cx="100" cy="100" r="${r}" class="track" stroke-dasharray="${arc} ${c}"/>
          <circle cx="100" cy="100" r="${r}" class="fill" stroke-dasharray="${fill} ${c}"/>
        </g>
      </svg>
      <div class="dial-inner">${inner}</div>
    </div>`;
}

const STYLE = `
  :host {
    --cm-heat-1: #ffa726; --cm-heat-2: #e53935; --cm-cool: #4fc3f7; --cm-ok: #66bb6a;
    --cm-soft: color-mix(in srgb, var(--primary-text-color) 8%, transparent);
    --cm-line: color-mix(in srgb, var(--primary-text-color) 14%, transparent);
    display: block;
  }
  ha-card { padding: 16px; overflow: hidden; }
  .head { display: flex; align-items: center; gap: 10px; margin-bottom: 4px; }
  .head ha-icon { color: var(--cm-heat-1); --mdc-icon-size: 22px; }
  .title { font-size: 18px; font-weight: 600; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .sub { color: var(--secondary-text-color); font-size: 13px; margin: 0 0 8px 32px; }
  .pill { font-size: 12px; font-weight: 600; padding: 4px 10px; border-radius: 999px; background: var(--cm-soft); color: var(--secondary-text-color); white-space: nowrap; }
  .pill.hot { background: color-mix(in srgb, var(--cm-heat-2) 22%, transparent); color: var(--cm-heat-1); }
  .pill.rest { background: color-mix(in srgb, var(--cm-cool) 22%, transparent); color: var(--cm-cool); }
  .pill.done { background: color-mix(in srgb, var(--cm-ok) 24%, transparent); color: var(--cm-ok); }
  .pill.bad { background: color-mix(in srgb, var(--error-color) 24%, transparent); color: var(--error-color); }

  .dial { position: relative; width: min(230px, 68%); margin: 6px auto 2px; aspect-ratio: 1; }
  .dial svg { width: 100%; height: 100%; display: block; }
  .dial .track { stroke: var(--cm-soft); }
  .dial .fill { stroke: url(#heat); transition: stroke-dasharray 0.8s ease; }
  .dial.rest .fill { stroke: var(--cm-cool); }
  .dial.done .fill { stroke: var(--cm-ok); }
  .dial.off .fill { stroke: transparent; }
  .dial-inner { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; }
  .big { font-size: 54px; font-weight: 700; line-height: 1; letter-spacing: -1px; font-variant-numeric: tabular-nums; }
  .cap { font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--secondary-text-color); margin-bottom: 4px; }
  .under { font-size: 14px; color: var(--secondary-text-color); margin-top: 6px; }

  .stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin: 10px 0; }
  .stat { background: var(--cm-soft); border-radius: 14px; padding: 10px 8px; text-align: center; min-width: 0; }
  .stat .v { font-size: 18px; font-weight: 600; font-variant-numeric: tabular-nums; white-space: nowrap; }
  .stat .l { font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--secondary-text-color); margin-bottom: 2px; }
  .stat .s { font-size: 12px; color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .stat.warn .v { color: var(--cm-heat-2); }

  .banner { border-radius: 12px; padding: 10px 12px; margin: 8px 0; font-weight: 600; font-size: 14px; display: flex; gap: 8px; align-items: center; }
  .banner.done { background: color-mix(in srgb, var(--cm-ok) 20%, transparent); color: var(--cm-ok); }
  .banner.bad { background: color-mix(in srgb, var(--error-color) 20%, transparent); color: var(--error-color); }
  .banner.note { background: var(--cm-soft); color: var(--secondary-text-color); font-weight: 500; }

  .chart { margin: 6px 0 2px; }
  .chart svg { width: 100%; height: 92px; display: block; }
  .chart .meat { fill: none; stroke: url(#heatline); stroke-width: 2.5; stroke-linejoin: round; stroke-linecap: round; }
  .chart .grill { fill: none; stroke: var(--secondary-text-color); stroke-width: 1.5; opacity: 0.6; stroke-linejoin: round; }
  .chart .goal { stroke: var(--cm-ok); stroke-width: 1; stroke-dasharray: 4 4; opacity: 0.8; }
  .legend { display: flex; gap: 14px; font-size: 11px; color: var(--secondary-text-color); justify-content: center; }
  .legend i { display: inline-block; width: 10px; height: 3px; border-radius: 2px; margin-right: 5px; vertical-align: middle; }

  .stages { display: flex; gap: 6px; flex-wrap: wrap; margin: 8px 0; }
  .stage { font-size: 12px; padding: 4px 10px; border-radius: 999px; background: var(--cm-soft); color: var(--secondary-text-color); }
  .stage.now { background: color-mix(in srgb, var(--cm-heat-1) 26%, transparent); color: var(--primary-text-color); font-weight: 600; }
  .stage.past { opacity: 0.55; text-decoration: line-through; }

  .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .grid3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
  .field { background: var(--cm-soft); border-radius: 14px; padding: 8px 10px; min-width: 0; margin-top: 8px; }
  .grid2 .field, .grid3 .field { margin-top: 0; }
  .grid2, .grid3 { margin-top: 8px; }
  .field label { display: flex; justify-content: space-between; gap: 6px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--secondary-text-color); margin-bottom: 2px; }
  .field label b { color: var(--cm-heat-1); font-weight: 600; letter-spacing: 0; text-transform: none; }
  select, input {
    width: 100%; box-sizing: border-box; background: transparent; color: var(--primary-text-color);
    border: 0; outline: 0; font: inherit; font-size: 15px; font-weight: 500; padding: 4px 0; min-width: 0;
  }
  select { appearance: none; -webkit-appearance: none; cursor: pointer; text-overflow: ellipsis;
    background-image: linear-gradient(45deg, transparent 50%, var(--secondary-text-color) 50%), linear-gradient(135deg, var(--secondary-text-color) 50%, transparent 50%);
    background-position: calc(100% - 9px) 55%, calc(100% - 4px) 55%; background-size: 5px 5px; background-repeat: no-repeat; padding-right: 18px; }
  select option { background: var(--card-background-color); color: var(--primary-text-color); }
  input::placeholder { color: var(--secondary-text-color); opacity: 0.7; }
  .step { display: flex; align-items: center; gap: 2px; }
  .step input { text-align: center; font-variant-numeric: tabular-nums; font-weight: 600; -moz-appearance: textfield; }
  .step input::-webkit-outer-spin-button, .step input::-webkit-inner-spin-button { -webkit-appearance: none; margin: 0; }
  .step button { flex: none; width: 30px; height: 30px; border-radius: 50%; border: 0; background: var(--cm-line); color: var(--primary-text-color); font-size: 18px; line-height: 1; cursor: pointer; }
  .step .unit { font-size: 12px; color: var(--secondary-text-color); flex: none; }

  .actions { display: flex; gap: 8px; margin-top: 12px; }
  .btn { flex: 1; border: 0; border-radius: 14px; padding: 13px 10px; font: inherit; font-size: 15px; font-weight: 600; cursor: pointer;
    background: var(--cm-soft); color: var(--primary-text-color); display: flex; align-items: center; justify-content: center; gap: 6px; }
  .btn ha-icon { --mdc-icon-size: 20px; }
  .btn.go { background: linear-gradient(135deg, var(--cm-heat-1), var(--cm-heat-2)); color: #fff; }
  .btn.stop { color: var(--error-color); }
  .btn.on { background: color-mix(in srgb, var(--cm-heat-1) 28%, transparent); color: var(--cm-heat-1); }
  .btn:disabled { opacity: 0.45; cursor: default; }
  .bar { height: 8px; border-radius: 4px; background: var(--cm-soft); overflow: hidden; margin-top: 6px; }
  .bar i { display: block; height: 100%; border-radius: 4px; background: linear-gradient(90deg, var(--cm-heat-1), var(--cm-heat-2)); transition: width 0.6s ease; }
  .chips { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
  .chip { font-size: 13px; padding: 5px 10px; border-radius: 999px; background: var(--cm-soft); }
  .empty { color: var(--secondary-text-color); padding: 8px 0; }

  ha-card.summary { padding: 6px 14px; cursor: pointer; }
  .row { display: flex; align-items: center; gap: 12px; padding: 8px 0; }
  .row + .row { border-top: 1px solid var(--cm-line); }
  .row .dial { width: 46px; margin: 0; flex: none; }
  .row .dial svg g { stroke-width: 20px; }
  .row .dial-inner ha-icon { --mdc-icon-size: 18px; color: var(--cm-heat-1); }
  .row .dial.rest ha-icon { color: var(--cm-cool); }
  .row .dial.done ha-icon { color: var(--cm-ok); }
  .row .dial.off ha-icon { color: var(--secondary-text-color); }
  .row .mid { flex: 1; min-width: 0; }
  .row .name { font-weight: 600; font-size: 15px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .row .what { font-size: 12.5px; color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .row .what.bad { color: var(--error-color); }
  .row .what.good { color: var(--cm-ok); }
  .row .temp { text-align: right; flex: none; }
  .row .temp .t { font-size: 24px; font-weight: 700; line-height: 1.05; font-variant-numeric: tabular-nums; }
  .row .temp .of { font-size: 12px; color: var(--secondary-text-color); }
`;

class CookManagerBase extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._signature = null;
    this.shadowRoot.addEventListener("click", (event) => this._onClick(event));
    this.shadowRoot.addEventListener("change", (event) => this._onChange(event));
    this.shadowRoot.addEventListener("focusout", () => setTimeout(() => this._update(), 0));
  }

  set hass(hass) {
    this._hass = hass;
    this._update();
  }

  getCardSize() {
    return 7;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: "auto" };
  }

  _state(entityId) {
    return entityId ? this._hass.states[entityId] : undefined;
  }

  _call(domain, service, entityId, data) {
    return this._hass.callService(domain, service, { entity_id: entityId, ...(data || {}) });
  }

  /* Render only when something shown has changed, and never under the user's fingers. */
  _update(force) {
    if (!this._hass || !this._config) return;
    const active = this.shadowRoot.activeElement;
    if (!force && active && ["SELECT", "INPUT"].includes(active.tagName)) return;
    const signature = this._collect();
    if (!force && signature === this._signature) return;
    this._signature = signature;
    this.shadowRoot.innerHTML = `<style>${STYLE}</style><ha-card>${this._render()}</ha-card>`;
  }

  _select(key, entityId, label, extra) {
    const state = this._state(entityId);
    if (!state) return "";
    const options = (state.attributes.options || [])
      .map((opt) => `<option${opt === state.state ? " selected" : ""}>${esc(opt)}</option>`)
      .join("");
    return `<div class="field"><label><span>${label}</span>${extra ? `<b>${extra}</b>` : ""}</label>
      <select data-select="${entityId}">${options}</select></div>`;
  }

  _stepper(entityId, label, unit) {
    const state = this._state(entityId);
    if (!state) return "";
    const a = state.attributes;
    return `<div class="field"><label><span>${label}</span></label><div class="step">
      <button data-step="${entityId}" data-dir="-1" aria-label="Lower ${label}">−</button>
      <input type="number" inputmode="decimal" data-number="${entityId}" value="${esc(num(state.state) ?? "")}"
        min="${a.min}" max="${a.max}" step="${a.step}" aria-label="${label}"><span class="unit">${unit}</span>
      <button data-step="${entityId}" data-dir="1" aria-label="Raise ${label}">+</button></div></div>`;
  }

  _setNumber(entityId, value) {
    const state = this._state(entityId);
    if (!state || value === null) return;
    const a = state.attributes;
    const step = a.step || 1;
    let next = Math.round(value / step) * step;
    next = Math.max(a.min ?? next, Math.min(a.max ?? next, next));
    this._call("number", "set_value", entityId, { value: Number(next.toFixed(2)) });
  }

  _onChange(event) {
    const el = event.target;
    if (el.dataset.select) this._call("select", "select_option", el.dataset.select, { option: el.value });
    else if (el.dataset.number) this._setNumber(el.dataset.number, num(el.value));
    else if (el.dataset.text) this._call("text", "set_value", el.dataset.text, { value: el.value });
    else return;
    el.blur();
  }

  _onClick(event) {
    const el = event.target.closest("[data-step],[data-press],[data-act]");
    if (!el) return;
    if (el.dataset.step) {
      const state = this._state(el.dataset.step);
      if (state) this._setNumber(el.dataset.step, (num(state.state) ?? 0) + Number(el.dataset.dir) * (state.attributes.step || 1));
    } else if (el.dataset.press) {
      if (el.dataset.confirm && !window.confirm(el.dataset.confirm)) return;
      this._call("button", "press", el.dataset.press);
    } else if (el.dataset.act) {
      this._action(el.dataset.act);
    }
  }

  _action() {}
}

class CookManagerCookCard extends CookManagerBase {
  static getStubConfig() {
    return { slot: 1 };
  }

  setConfig(config) {
    const slot = Number(config.slot || 1);
    if (!Number.isInteger(slot) || slot < 1) throw new Error("slot must be a cook slot number");
    this._config = { ...config, slot };
    this._history = null;
    this._historyKey = null;
    this._signature = null;
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    this._timer = null;
  }

  connectedCallback() {
    if (!this._timer) this._timer = setInterval(() => this._loadHistory(true), 60000);
  }

  _ids() {
    const slot = this._config.slot;
    return deviceEntities(this._hass, (id) => id.endsWith(`_slot${slot}`), SLOT_DEFAULTS(slot));
  }

  _collect() {
    const ids = (this._e = this._ids());
    const parts = Object.values(ids).map((id) => {
      const s = this._state(id);
      return s ? [s.state, s.attributes.options, s.attributes.stage, s.attributes.grill_state, s.attributes.stalled, s.attributes.probe_lost, s.attributes.cook_name, s.attributes.target] : null;
    });
    return JSON.stringify([parts, this._history ? this._history.stamp : 0]);
  }

  async _loadHistory(force) {
    const status = this._hass && this._e ? this._state(this._e.status) : null;
    const started = status && status.attributes.started;
    if (!started || status.state === "idle") {
      this._history = null;
      this._historyKey = null;
      return;
    }
    if (!force && this._historyKey === started) return;
    this._historyKey = started;
    const ids = [this._e.temperature, this._e.grill_temperature];
    try {
      const result = await this._hass.callWS({
        type: "history/history_during_period",
        start_time: started,
        end_time: new Date().toISOString(),
        entity_ids: ids,
        minimal_response: true,
        no_attributes: true,
        significant_changes_only: false,
      });
      const series = ids.map((id) =>
        (result[id] || []).map((p) => [p.lu * 1000, num(p.s)]).filter((p) => p[1] !== null)
      );
      this._history = { started: new Date(started).getTime(), meat: series[0], grill: series[1], stamp: Date.now() };
      this._update();
    } catch (err) {
      /* history is a nicety; the card works without it */
    }
  }

  _chart(target) {
    const h = this._history;
    if (!h || h.meat.length < 3) return "";
    const W = 320;
    const H = 92;
    const t0 = h.started;
    const t1 = Math.max(Date.now(), t0 + 60000);
    const all = [...h.meat, ...h.grill].map((p) => p[1]);
    if (target !== null) all.push(target);
    const lo = Math.min(...all) - 3;
    const hi = Math.max(...all) + 3;
    const x = (t) => ((Math.max(t, t0) - t0) / (t1 - t0)) * W;
    const y = (v) => H - ((v - lo) / (hi - lo)) * H;
    const line = (points) => points.map((p, i) => `${i ? "L" : "M"}${x(p[0]).toFixed(1)} ${y(p[1]).toFixed(1)}`).join(" ");
    return `<div class="chart"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Temperature curve">
        <defs><linearGradient id="heatline" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stop-color="var(--cm-heat-1)"/><stop offset="1" stop-color="var(--cm-heat-2)"/></linearGradient></defs>
        ${target !== null ? `<line class="goal" x1="0" x2="${W}" y1="${y(target).toFixed(1)}" y2="${y(target).toFixed(1)}"/>` : ""}
        ${h.grill.length > 1 ? `<path class="grill" d="${line(h.grill)}"/>` : ""}
        <path class="meat" d="${line(h.meat)}"/>
      </svg>
      <div class="legend"><span><i style="background:var(--cm-heat-2)"></i>Meat</span>
        ${h.grill.length > 1 ? `<span><i style="background:var(--secondary-text-color)"></i>Grill</span>` : ""}
        ${target !== null ? `<span><i style="background:var(--cm-ok)"></i>Pull at ${deg(target)}</span>` : ""}</div></div>`;
  }

  _render() {
    const e = this._e;
    const status = this._state(e.status);
    if (!status) return `<div class="empty">Cook slot ${this._config.slot} was not found. Is Cook Manager set up?</div>`;
    if (status.state === "unavailable") return `<div class="empty">Cook Manager is not running.</div>`;
    return status.state === "idle" ? this._renderSetup(status) : this._renderCook(status);
  }

  _renderSetup(status) {
    const e = this._e;
    const slot = this._config.slot;
    const name = this._state(e.name);
    const meatNow = num(this._state(e.temperature)?.state);
    const grillNow = num(this._state(e.grill_temperature)?.state);
    const recipe = this._state(e.recipe)?.state;
    const doneness = this._state(e.doneness);
    const hasDoneness = doneness && (doneness.attributes.options || []).length > 1;
    const probe = this._state(e.probe)?.state;
    const noProbe = !probe || probe === "None";
    return `
      <div class="head"><ha-icon icon="mdi:grill"></ha-icon><div class="title">Cook ${slot}</div><span class="pill">Ready to set up</span></div>
      <div class="field"><label><span>Name</span></label>
        <input type="text" data-text="${e.name}" value="${esc(name && !["unknown", "unavailable"].includes(name.state) ? name.state : "")}"
          placeholder="${esc(recipe && recipe !== "Manual" ? recipe : "What are you cooking?")}" maxlength="60" aria-label="Cook name"></div>
      <div class="${hasDoneness ? "grid2" : ""}">
        ${this._select("recipe", e.recipe, "Recipe")}
        ${hasDoneness ? this._select("doneness", e.doneness, "Doneness") : ""}
      </div>
      <div class="grid2">
        ${this._select("probe", e.probe, "Meat probe", meatNow !== null ? deg(meatNow) : "")}
        ${this._select("grill_probe", e.grill_probe, "Grill probe", grillNow !== null ? deg(grillNow) : "")}
      </div>
      <div class="grid2">
        ${this._stepper(e.target, "Meat target", "°")}
        ${this._stepper(e.grill_target, "Grill target", "°")}
      </div>
      <div class="grid2">
        ${this._select("grill_controller", e.grill_controller, "Grill controller")}
        ${this._stepper(e.weight, "Weight", "kg")}
      </div>
      ${noProbe ? `<div class="banner note">Plug in a probe and pick it as the meat probe to track the cook.</div>` : ""}
      <div class="actions"><button class="btn go" data-press="${e.start}"><ha-icon icon="mdi:play"></ha-icon>Start cook</button></div>`;
  }

  _renderCook(status) {
    const e = this._e;
    const a = status.attributes;
    const state = status.state;
    this._loadHistory(false);
    const meat = num(this._state(e.temperature)?.state);
    const grill = num(this._state(e.grill_temperature)?.state);
    const remaining = num(this._state(e.time_remaining)?.state);
    const finish = this._state(e.finish_time)?.state;
    const progress = num(this._state(e.progress)?.state);
    const pull = num(a.pull_temperature);
    const target = num(a.target);
    const grillTarget = num(a.grill_target);
    const name = a.cook_name || `Cook ${this._config.slot}`;
    const pill = { cooking: ["hot", "Cooking"], resting: ["rest", "Resting"], done: ["done", "Done"] }[state] || ["", state];

    let under = pull !== null ? `pull at ${deg(pull)}` : "";
    if (state === "cooking" && target !== null && pull !== null && Math.round(target) !== Math.round(pull)) under += ` · ${deg(target)} after rest`;
    if (state === "resting") under = "off the heat";
    if (state === "done") under = a.max_temperature ? `peaked at ${deg(num(a.max_temperature))}` : "ready";
    const fraction = state === "cooking" ? (progress ?? 0) / 100 : 1;
    const centre = `<div class="cap">${state === "resting" ? "Resting" : "Meat"}</div><div class="big">${deg(meat)}</div><div class="under">${under}</div>`;

    const banners = [];
    if (state === "done") banners.push(`<div class="banner done"><ha-icon icon="mdi:check-circle"></ha-icon>${esc(name)} is ready.</div>`);
    if (a.probe_lost) banners.push(`<div class="banner bad"><ha-icon icon="mdi:alert"></ha-icon>No reading from the meat probe.</div>`);
    if (a.stalled) banners.push(`<div class="banner note">In the stall. No time estimate until the temperature climbs again.</div>`);

    const grillWarn = ["low", "high"].includes(a.grill_state);
    const timeLabel = state === "resting" ? "Rest left" : "Time left";
    const stats = `<div class="stats">
        <div class="stat${grillWarn ? " warn" : ""}"><div class="l">Grill</div><div class="v">${deg(grill)}</div>
          <div class="s">${grillTarget !== null ? `target ${deg(grillTarget)}` : "no target"}${grillWarn ? ` · ${a.grill_state}` : ""}</div></div>
        <div class="stat"><div class="l">${timeLabel}</div><div class="v">${state === "done" ? "0 min" : duration(remaining)}</div>
          <div class="s">${clock(finish) && remaining !== null ? `about ${clock(finish)}` : a.stalled ? "stalled" : state === "done" ? "finished" : "working it out"}</div></div>
        <div class="stat"><div class="l">Rate</div><div class="v">${a.rate_per_hour !== null && a.rate_per_hour !== undefined ? `${a.rate_per_hour > 0 ? "+" : ""}${Math.round(a.rate_per_hour)}°` : "--"}</div>
          <div class="s">per hour</div></div>
      </div>`;

    const stages = (a.stages || []).length > 1
      ? `<div class="stages">${a.stages.map((s, i) => `<span class="stage${i + 1 === a.stage_number ? " now" : i + 1 < a.stage_number ? " past" : ""}">${esc(s)}</span>`).join("")}</div>`
      : "";
    const moreStages = (a.stage_number || 1) < (a.stage_count || 1);

    return `
      <div class="head"><ha-icon icon="mdi:grill"></ha-icon><div class="title">${esc(name)}</div><span class="pill ${pill[0]}">${pill[1]}</span></div>
      <div class="sub">${esc([a.recipe && a.recipe !== "Manual" ? a.recipe : null, a.doneness].filter(Boolean).join(" · "))}</div>
      ${banners.join("")}
      ${dial(fraction, centre, state === "cooking" ? "" : state === "resting" ? "rest" : "done")}
      ${stats}
      ${this._chart(pull)}
      ${stages}
      ${state === "cooking" ? `<div class="grid2">${this._stepper(e.target, "Meat target", "°")}${this._stepper(e.grill_target, "Grill target", "°")}</div>` : ""}
      <div class="actions">
        ${moreStages ? `<button class="btn" data-press="${e.next_stage}"><ha-icon icon="mdi:skip-next"></ha-icon>Next stage</button>` : ""}
        ${state === "done"
          ? `<button class="btn go" data-press="${e.stop}"><ha-icon icon="mdi:check"></ha-icon>Finish</button>`
          : `<button class="btn stop" data-press="${e.stop}" data-confirm="Stop this cook?"><ha-icon icon="mdi:stop"></ha-icon>Stop</button>`}
      </div>`;
  }
}

class CookManagerIkamandCard extends CookManagerBase {
  static getStubConfig() {
    return {};
  }

  setConfig(config) {
    this._config = { ...config };
    this._signature = null;
  }

  getCardSize() {
    return 6;
  }

  _collect() {
    const host = this._config.host;
    this._e = deviceEntities(this._hass, (id) => id.startsWith("ikamand_") && (!host || id === `ikamand_${host}`), IKAMAND_DEFAULTS);
    return JSON.stringify(
      Object.values(this._e).map((id) => {
        const s = this._state(id);
        return s ? [s.state, s.attributes.temperature, s.attributes.current_temperature, s.attributes.fire_it_up] : null;
      })
    );
  }

  _action(act) {
    const e = this._e;
    const climate = this._state(e.ikamand_climate);
    if (!climate) return;
    if (act === "power") {
      this._call("climate", "set_hvac_mode", e.ikamand_climate, { hvac_mode: climate.state === "off" ? "heat" : "off" });
    } else if (act === "boost") {
      const boost = this._state(e.ikamand_boost);
      this._call("switch", boost && boost.state === "on" ? "turn_off" : "turn_on", e.ikamand_boost);
    } else if (act === "up" || act === "down") {
      const a = climate.attributes;
      const next = Math.max(a.min_temp, Math.min(a.max_temp, (num(a.temperature) ?? 110) + (act === "up" ? 5 : -5)));
      this._call("climate", "set_temperature", e.ikamand_climate, { temperature: next });
    }
  }

  _render() {
    const e = this._e;
    const climate = this._state(e.ikamand_climate);
    if (!climate) return `<div class="empty">No iKamand is set up in Cook Manager.</div>`;
    const offline = climate.state === "unavailable";
    const running = climate.state === "heat";
    const boost = this._state(e.ikamand_boost)?.state === "on";
    const pit = num(climate.attributes.current_temperature);
    const target = num(climate.attributes.temperature);
    const fan = num(this._state(e.ikamand_fan)?.state) ?? 0;
    const minutes = num(this._state(e.ikamand_boost_minutes)?.state);
    const pill = offline ? ["bad", "Offline"] : boost ? ["hot", "Fire it up"] : running ? ["hot", `Holding ${deg(target)}`] : ["", "Off"];
    const head = `<div class="head"><ha-icon icon="mdi:fan"></ha-icon><div class="title">iKamand</div><span class="pill ${pill[0]}">${pill[1]}</span></div>`;
    if (offline) return `${head}<div class="banner note">Not answering. Check that it has power and is on the Wi-Fi.</div>`;

    const fraction = running && target ? (pit ?? 0) / target : 0;
    const centre = `<div class="cap">Pit</div><div class="big">${deg(pit)}</div>
      <div class="under">${running ? `target ${deg(boost ? 260 : target)}` : "fan off"}</div>`;
    const probes = ["ikamand_p1", "ikamand_p2", "ikamand_p3"]
      .map((key, i) => [i + 1, num(this._state(e[key])?.state)])
      .filter((p) => p[1] !== null)
      .map((p) => `<span class="chip">Probe ${p[0]} <b>${deg(p[1])}</b></span>`)
      .join("");
    return `${head}
      ${dial(fraction, centre, running ? "" : "off")}
      <div class="field"><label><span>Fan</span><b>${Math.round(fan)} %</b></label><div class="bar"><i style="width:${Math.max(0, Math.min(100, fan))}%"></i></div></div>
      ${probes ? `<div class="chips">${probes}</div>` : ""}
      <div class="grid2">
        <div class="field"><label><span>Pit target</span></label><div class="step">
          <button data-act="down" aria-label="Lower pit target">−</button>
          <input type="number" value="${target ?? ""}" disabled aria-label="Pit target"><span class="unit">°</span>
          <button data-act="up" aria-label="Raise pit target">+</button></div></div>
        ${this._stepper(e.ikamand_boost_minutes, "Fire it up for", "min")}
      </div>
      <div class="actions">
        <button class="btn${running ? " on" : ""}" data-act="power"><ha-icon icon="mdi:power"></ha-icon>${running ? "Turn off" : "Hold " + deg(target)}</button>
        <button class="btn${boost ? " on" : ""}" data-act="boost"><ha-icon icon="mdi:fire"></ha-icon>${boost ? "Stop boost" : `Fire it up${minutes ? ` ${Math.round(minutes)} min` : ""}`}</button>
      </div>`;
  }
}

class CookManagerSummaryCard extends CookManagerBase {
  static getStubConfig() {
    return { show_idle: true };
  }

  setConfig(config) {
    this._config = { ...config };
    this._signature = null;
  }

  getCardSize() {
    return 2;
  }

  _slots() {
    const slots = [];
    for (let n = 1; n <= 8; n += 1) {
      const ids = deviceEntities(this._hass, (id) => id.endsWith(`_slot${n}`), SLOT_DEFAULTS(n));
      const status = this._state(ids.status);
      if (status && ["cooking", "resting", "done"].includes(status.state)) slots.push({ n, ids, status });
    }
    return slots;
  }

  _collect() {
    this._active = this._slots();
    return JSON.stringify(
      this._active.map((slot) => [
        slot.n,
        slot.status.state,
        slot.status.attributes,
        ...["temperature", "time_remaining", "finish_time", "progress"].map((key) => this._state(slot.ids[key])?.state),
      ])
    );
  }

  _update(force) {
    super._update(force);
    const card = this.shadowRoot.querySelector("ha-card");
    if (card) card.classList.add("summary");
    this.style.display = this._active && !this._active.length && !this._config.show_idle ? "none" : "";
  }

  _onClick() {
    const path = this._config.navigation_path;
    if (!path) return;
    window.history.pushState(null, "", path);
    window.dispatchEvent(new CustomEvent("location-changed", { bubbles: true, composed: true }));
  }

  _render() {
    if (!this._active.length) {
      return `<div class="row">${dial(0, '<ha-icon icon="mdi:grill"></ha-icon>', "off")}
        <div class="mid"><div class="name">BBQ</div><div class="what">Nothing cooking${this._config.navigation_path ? " · tap to start a cook" : ""}</div></div></div>`;
    }
    return this._active
      .map(({ n, ids, status }) => {
        const a = status.attributes;
        const state = status.state;
        const meat = num(this._state(ids.temperature)?.state);
        const remaining = num(this._state(ids.time_remaining)?.state);
        const finish = this._state(ids.finish_time)?.state;
        const progress = num(this._state(ids.progress)?.state);
        const pull = num(a.pull_temperature);
        let what = "Cooking";
        let tone = "";
        if (state === "done") [what, tone] = ["Ready", "good"];
        else if (a.probe_lost) [what, tone] = ["No reading from the probe", "bad"];
        else if (state === "resting") what = remaining !== null ? `Resting · ${duration(remaining)} left` : "Resting";
        else if (a.stalled) what = "In the stall";
        else if (remaining !== null) what = `${duration(remaining)} left${clock(finish) ? ` · about ${clock(finish)}` : ""}`;
        else what = "Cooking · working out the time";
        if (!tone && ["low", "high"].includes(a.grill_state)) [what, tone] = [`Grill ${a.grill_state} · ${what}`, "bad"];
        const icon = state === "done" ? "mdi:check" : state === "resting" ? "mdi:timer-sand" : "mdi:grill";
        return `<div class="row">
          ${dial(state === "cooking" ? (progress ?? 0) / 100 : 1, `<ha-icon icon="${icon}"></ha-icon>`, state === "cooking" ? "" : state === "resting" ? "rest" : "done")}
          <div class="mid"><div class="name">${esc(a.cook_name || `Cook ${n}`)}</div><div class="what ${tone}">${esc(what)}</div></div>
          <div class="temp"><div class="t">${deg(meat)}</div>${state === "cooking" && pull !== null ? `<div class="of">of ${deg(pull)}</div>` : ""}</div>
        </div>`;
      })
      .join("");
  }
}

if (!customElements.get("cook-manager-cook-card")) customElements.define("cook-manager-cook-card", CookManagerCookCard);
if (!customElements.get("cook-manager-ikamand-card")) customElements.define("cook-manager-ikamand-card", CookManagerIkamandCard);
if (!customElements.get("cook-manager-summary-card")) customElements.define("cook-manager-summary-card", CookManagerSummaryCard);

window.customCards = window.customCards || [];
window.customCards.push(
  { type: "cook-manager-cook-card", name: "Cook Manager: cook", description: "Set up and follow one cook slot." },
  { type: "cook-manager-ikamand-card", name: "Cook Manager: iKamand", description: "Pit temperature, fan and Fire it up." },
  { type: "cook-manager-summary-card", name: "Cook Manager: summary", description: "One line for each running cook." }
);
