/**
 * MojiPic card for Home Assistant.
 *
 * Shows the display's live image and lets you change it without writing YAML --
 * the point of this integration is event -> image, so picking an image from a
 * dashboard should not require editing an automation.
 *
 * Register as a Lovelace resource:
 *   /local/mojipic-card.js
 */
class MojipicCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._images = [];
    this._selected = null;
    this._busy = false;
    this._error = null;
  }

  setConfig(config) {
    this._config = config;
    this._imageEntity = config.image_entity || "";
    this._selectEntity = config.select_entity || "";
    this._title = config.title || "";
  }

  getCardSize() {
    return 3;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) {
      this._loadImages();
      this._interval = setInterval(() => this._loadImages(), 30000);
    }
  }

  connectedCallback() {
    this._render();
  }

  disconnectedCallback() {
    if (this._interval) clearInterval(this._interval);
  }

  async _loadImages() {
    if (!this._hass || !this._selectEntity) return;
    try {
      const state = this._hass.states[this._selectEntity];
      if (!state) return;
      const options = state.attributes.options || [];
      if (JSON.stringify(options) !== JSON.stringify(this._images)) {
        this._images = options;
        this._render();
      }
    } catch (e) {
      // Entity not available yet; the next tick will pick it up.
    }
  }

  async _select(value) {
    if (!this._hass || this._busy) return;
    this._busy = true;
    this._error = null;
    this._render();
    try {
      await this._hass.callService("select", "select_option", {
        entity_id: this._selectEntity,
        option: value,
      });
      this._selected = value;
    } catch (e) {
      this._error = e && e.message ? e.message : String(e);
    } finally {
      this._busy = false;
      this._render();
    }
  }

  _render() {
    if (!this.shadowRoot) return;
    const hass = this._hass;
    const title = this._title || (hass ? "MojiPic" : "MojiPic");

    let preview = "";
    if (this._imageEntity && hass && hass.states[this._imageEntity]) {
      const imgEntity = hass.states[this._imageEntity];
      if (imgEntity.attributes && imgEntity.attributes.entity_picture) {
        preview = `<img class="preview" src="${imgEntity.attributes.entity_picture}" alt="displayed image" />`;
      }
    }
    if (!preview) {
      preview = '<div class="preview placeholder">no image yet</div>';
    }

    let status = "";
    if (this._error) {
      status = `<div class="error">${this._error}</div>`;
    } else if (this._busy) {
      status = '<div class="status">sending&hellip; a push takes a few seconds</div>';
    } else if (hass && this._selectEntity && hass.states[this._selectEntity]) {
      const state = hass.states[this._selectEntity];
      status = `<div class="status">showing ${state.state} &middot; ${state.attributes.connection_state || ""}</div>`;
    }

    let options = '<div class="hint">Upload images with the "Upload image" action, or via Settings &rarr; Devices &amp; Services.</div>';
    if (this._images.length) {
      options = `<div class="options">${this._images
        .map(
          (id) =>
            `<button class="chip" data-id="${id}">${id}</button>`
        )
        .join("")}</div>`;
    }

    this.shadowRoot.innerHTML = `
      <style>
        ha-card { padding: 12px; }
        .title { font-weight: 600; margin-bottom: 8px; }
        .preview { width: 128px; height: 128px; image-rendering: pixelated;
                   display: block; border-radius: 8px; background: #000; }
        .placeholder { display: flex; align-items: center; justify-content: center;
                       color: #888; font-size: 12px; }
        .status { margin-top: 8px; font-size: 12px; color: #666; }
        .error { margin-top: 8px; font-size: 12px; color: #db4437; }
        .hint { margin-top: 8px; font-size: 11px; color: #888; }
        .options { margin-top: 8px; display: flex; flex-wrap: wrap; gap: 6px; }
        .chip { padding: 4px 10px; border-radius: 12px; border: 1px solid #ccc;
                background: transparent; cursor: pointer; font-size: 12px; }
        .chip:hover { background: rgba(127,127,127,0.15); }
      </style>
      <ha-card>
        <div class="title">${title}</div>
        ${preview}
        ${status}
        ${options}
      </ha-card>
    `;

    this.shadowRoot.querySelectorAll(".chip").forEach((button) => {
      button.addEventListener("click", () => this._select(button.dataset.id));
    });
  }
}

customElements.define("mojipic-card", MojipicCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "mojipic-card",
  name: "MojiPic Card",
  description: "Show and change the image on a MojiPic display.",
  preview: true,
});
