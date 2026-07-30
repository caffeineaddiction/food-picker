/**
 * Procedural audio (SPEC.md §14).
 *
 * Everything is synthesised in Web Audio — there are no audio files to source,
 * license or ship. Music is three scheduled layers that fade in as the race
 * tightens; SFX are one-shot recipes; the crowd is filtered noise whose gain
 * follows the excitement of the race.
 *
 * Browsers block audio until a gesture, so `unlock()` is called from the host's
 * first click and everything before that is a silent no-op.
 */

const MASTER_GAIN = 0.55;

const SCALE = [0, 2, 3, 5, 7, 9, 10]; // minor-ish, reads as "sporting event"

export class AudioEngine {
  constructor() {
    this.ctx = null;
    this.enabled = false;
    this.master = null;
    this.musicGain = null;
    this.crowdGain = null;
    this.layers = [];
    this.schedulerTimer = null;
    this.nextNoteTime = 0;
    this.step = 0;
    this.tempo = 128;
    this.intensity = 0;
    this.hoofTimer = null;
    this.hoofRate = 0;
  }

  /** Must be called from a user gesture. Safe to call repeatedly. */
  unlock() {
    if (this.ctx) {
      if (this.ctx.state === "suspended") this.ctx.resume();
      return;
    }
    const Context = window.AudioContext || window.webkitAudioContext;
    if (!Context) return;
    this.ctx = new Context();
    this.master = this.ctx.createGain();
    this.master.gain.value = MASTER_GAIN;

    // A limiter keeps the office TV speakers safe when chaos peaks.
    const limiter = this.ctx.createDynamicsCompressor();
    limiter.threshold.value = -8;
    limiter.knee.value = 6;
    limiter.ratio.value = 12;
    limiter.attack.value = 0.003;
    limiter.release.value = 0.2;
    this.master.connect(limiter).connect(this.ctx.destination);

    this.musicGain = this.ctx.createGain();
    this.musicGain.gain.value = 0;
    this.musicGain.connect(this.master);

    this.crowdGain = this.ctx.createGain();
    this.crowdGain.gain.value = 0;
    this.crowdGain.connect(this.master);
    this.startCrowdBed();

    this.enabled = true;
  }

  get muted() {
    return !this.enabled || !this.ctx;
  }

  setMuted(muted) {
    if (!this.ctx) return;
    this.master.gain.linearRampToValueAtTime(
      muted ? 0 : MASTER_GAIN,
      this.ctx.currentTime + 0.15
    );
  }

  now() {
    return this.ctx ? this.ctx.currentTime : 0;
  }

  // ------------------------------------------------------------------ voices

  /** Basic enveloped oscillator — the workhorse for most SFX. */
  tone({
    freq = 440,
    type = "square",
    duration = 0.2,
    gain = 0.2,
    attack = 0.005,
    slideTo = null,
    delay = 0,
    destination = null,
    detune = 0,
  } = {}) {
    if (this.muted) return;
    const start = this.now() + delay;
    const osc = this.ctx.createOscillator();
    const envelope = this.ctx.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, start);
    if (detune) osc.detune.setValueAtTime(detune, start);
    if (slideTo) osc.frequency.exponentialRampToValueAtTime(Math.max(20, slideTo), start + duration);
    envelope.gain.setValueAtTime(0.0001, start);
    envelope.gain.exponentialRampToValueAtTime(gain, start + attack);
    envelope.gain.exponentialRampToValueAtTime(0.0001, start + duration);
    osc.connect(envelope).connect(destination || this.master);
    osc.start(start);
    osc.stop(start + duration + 0.05);
  }

  /** Filtered noise burst — hooves, crowd swells, splashes, impacts. */
  noise({
    duration = 0.2,
    gain = 0.2,
    filter = "bandpass",
    freq = 1200,
    q = 1,
    delay = 0,
    sweepTo = null,
  } = {}) {
    if (this.muted) return;
    const start = this.now() + delay;
    const frames = Math.floor(this.ctx.sampleRate * duration);
    const buffer = this.ctx.createBuffer(1, Math.max(1, frames), this.ctx.sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < frames; i += 1) data[i] = Math.random() * 2 - 1;

    const source = this.ctx.createBufferSource();
    source.buffer = buffer;
    const band = this.ctx.createBiquadFilter();
    band.type = filter;
    band.frequency.setValueAtTime(freq, start);
    band.Q.value = q;
    if (sweepTo) band.frequency.exponentialRampToValueAtTime(Math.max(60, sweepTo), start + duration);
    const envelope = this.ctx.createGain();
    envelope.gain.setValueAtTime(gain, start);
    envelope.gain.exponentialRampToValueAtTime(0.0001, start + duration);
    source.connect(band).connect(envelope).connect(this.master);
    source.start(start);
  }

  // ------------------------------------------------------------------- crowd

  startCrowdBed() {
    if (!this.ctx) return;
    const frames = this.ctx.sampleRate * 2;
    const buffer = this.ctx.createBuffer(1, frames, this.ctx.sampleRate);
    const data = buffer.getChannelData(0);
    let last = 0;
    for (let i = 0; i < frames; i += 1) {
      // Brown-ish noise reads as a distant crowd rather than static.
      last = (last + (Math.random() * 2 - 1) * 0.02) * 0.995;
      data[i] = last;
    }
    const source = this.ctx.createBufferSource();
    source.buffer = buffer;
    source.loop = true;
    const filter = this.ctx.createBiquadFilter();
    filter.type = "bandpass";
    filter.frequency.value = 700;
    filter.Q.value = 0.7;
    source.connect(filter).connect(this.crowdGain);
    source.start();
    this.crowdSource = source;
  }

  /** 0 = empty grandstand, 1 = final furlong. */
  setCrowd(level) {
    if (this.muted) return;
    this.crowdGain.gain.linearRampToValueAtTime(
      Math.min(0.5, level * 0.5),
      this.now() + 0.4
    );
  }

  cheer(strength = 1) {
    this.noise({ duration: 1.1, gain: 0.16 * strength, freq: 900, q: 0.6, sweepTo: 1600 });
  }

  // -------------------------------------------------------------------- hoof

  /** Hooves are driven by the leader's leg frequency, mixed low. */
  setHoofRate(rate) {
    this.hoofRate = rate;
    if (this.muted) return;
    if (!this.hoofTimer) this.hoofTimer = setInterval(() => this.hoofTick(), 60);
  }

  hoofTick() {
    if (this.muted || this.hoofRate <= 0) return;
    if (Math.random() > this.hoofRate * 0.16) return;
    this.noise({ duration: 0.06, gain: 0.05, filter: "lowpass", freq: 420, q: 1 });
  }

  stopHooves() {
    if (this.hoofTimer) clearInterval(this.hoofTimer);
    this.hoofTimer = null;
    this.hoofRate = 0;
  }

  // ------------------------------------------------------------------- music

  startMusic(style = "derby") {
    if (this.muted) return;
    this.style = style;
    this.tempo =
      style === "party" ? 142 : style === "candy" ? 118 : style === "space" ? 104 : 128;
    this.intensity = 0;
    this.step = 0;
    this.nextNoteTime = this.now() + 0.1;
    this.musicGain.gain.cancelScheduledValues(this.now());
    this.musicGain.gain.linearRampToValueAtTime(0.22, this.now() + 1.2);
    if (this.schedulerTimer) clearInterval(this.schedulerTimer);
    this.schedulerTimer = setInterval(() => this.scheduleMusic(), 25);
  }

  stopMusic(fade = 0.6) {
    if (this.schedulerTimer) clearInterval(this.schedulerTimer);
    this.schedulerTimer = null;
    if (this.muted) return;
    this.musicGain.gain.linearRampToValueAtTime(0.0001, this.now() + fade);
  }

  /** Race tension 0→1 fades in the countermelody and doubles the hats. */
  setIntensity(value) {
    this.intensity = Math.max(0, Math.min(1, value));
  }

  scheduleMusic() {
    if (this.muted) return;
    const secondsPerStep = 60 / this.tempo / 4;
    while (this.nextNoteTime < this.now() + 0.2) {
      this.playStep(this.step, this.nextNoteTime);
      this.nextNoteTime += secondsPerStep;
      this.step += 1;
    }
  }

  playStep(step, time) {
    const bar = Math.floor(step / 16) % 4;
    const beat = step % 16;
    const root = 55 * Math.pow(2, [0, 0, 1, 0][bar] * 0.0) * (this.intensity > 0.85 ? 1.122 : 1);
    const delay = Math.max(0, time - this.now());

    // Bass on every quarter
    if (beat % 4 === 0) {
      const degree = [0, 3, 4, 2][bar];
      this.tone({
        freq: root * Math.pow(2, SCALE[degree] / 12),
        type: "triangle",
        duration: 0.22,
        gain: 0.22,
        delay,
      });
    }
    // Off-beat hats; doubled at high intensity
    if (beat % 4 === 2 || (this.intensity > 0.5 && beat % 2 === 1)) {
      this.noise({ duration: 0.03, gain: 0.05, freq: 7000, q: 1.2, delay });
    }
    // Arpeggio
    if (beat % 2 === 0) {
      const note = SCALE[(beat / 2 + bar) % SCALE.length];
      this.tone({
        freq: root * 4 * Math.pow(2, note / 12),
        type: this.style === "synth" ? "sawtooth" : "square",
        duration: 0.12,
        gain: 0.055,
        delay,
      });
    }
    // Countermelody layer only once the race tightens
    if (this.intensity > 0.45 && beat % 8 === 4) {
      const note = SCALE[(bar * 2 + 3) % SCALE.length];
      this.tone({
        freq: root * 6 * Math.pow(2, note / 12),
        type: "triangle",
        duration: 0.3,
        gain: 0.06 * this.intensity,
        delay,
      });
    }
  }

  // --------------------------------------------------------------------- sfx

  countdownBeep(index) {
    this.tone({ freq: 440 + index * 110, type: "sine", duration: 0.28, gain: 0.3 });
  }

  gatesOpen() {
    this.noise({ duration: 0.5, gain: 0.32, filter: "highpass", freq: 300, sweepTo: 2400 });
    this.tone({ freq: 180, type: "sawtooth", duration: 0.4, gain: 0.22, slideTo: 90 });
    this.cheer(1.2);
  }

  /** Powerup cast sting, escalating with rarity. */
  cast(tier) {
    switch (tier) {
      case "epic":
        this.tone({ freq: 300, type: "sawtooth", duration: 0.5, gain: 0.2, slideTo: 1400 });
        this.tone({ freq: 90, type: "sine", duration: 0.7, gain: 0.3, delay: 0.18 });
        this.noise({ duration: 0.6, gain: 0.14, freq: 2400, sweepTo: 400, delay: 0.15 });
        break;
      case "rare":
        this.tone({ freq: 420, type: "square", duration: 0.35, gain: 0.16, slideTo: 1100 });
        break;
      case "uncommon":
        [0, 4, 7].forEach((semitone, index) =>
          this.tone({
            freq: 520 * Math.pow(2, semitone / 12),
            type: "triangle",
            duration: 0.22,
            gain: 0.13,
            delay: index * 0.045,
          })
        );
        break;
      default:
        this.tone({ freq: 760, type: "square", duration: 0.12, gain: 0.12 });
    }
  }

  grant() {
    this.tone({ freq: 880, type: "sine", duration: 0.14, gain: 0.16 });
    this.tone({ freq: 1320, type: "sine", duration: 0.18, gain: 0.12, delay: 0.09 });
  }

  stumble() {
    // Slide whistle down + thud: the sound of comedy misfortune.
    this.tone({ freq: 1200, type: "sine", duration: 0.42, gain: 0.18, slideTo: 260 });
    this.noise({ duration: 0.18, gain: 0.22, filter: "lowpass", freq: 260, delay: 0.28 });
  }

  freeze() {
    this.tone({ freq: 200, type: "sawtooth", duration: 0.6, gain: 0.26, slideTo: 150 });
    this.noise({ duration: 0.35, gain: 0.2, filter: "highpass", freq: 3000, sweepTo: 800 });
  }

  shield() {
    this.tone({ freq: 1500, type: "sine", duration: 0.3, gain: 0.16, slideTo: 900 });
  }

  ghost() {
    this.tone({ freq: 600, type: "sine", duration: 0.5, gain: 0.12, slideTo: 300, detune: 30 });
  }

  moo() {
    // An honest moo is impossible; a descending square glide is funnier anyway.
    this.tone({ freq: 220, type: "square", duration: 0.55, gain: 0.22, slideTo: 120 });
    this.tone({ freq: 110, type: "sawtooth", duration: 0.5, gain: 0.12, delay: 0.1 });
  }

  eventSting(shake = 0) {
    this.tone({ freq: 300, type: "square", duration: 0.2, gain: 0.16, slideTo: 520 });
    if (shake > 0.5) {
      this.noise({ duration: 0.7, gain: 0.28, filter: "lowpass", freq: 200, sweepTo: 60 });
    }
  }

  telegraph() {
    this.tone({ freq: 620, type: "triangle", duration: 0.12, gain: 0.12 });
    this.tone({ freq: 780, type: "triangle", duration: 0.14, gain: 0.1, delay: 0.13 });
  }

  shutter() {
    for (let i = 0; i < 5; i += 1) {
      this.noise({ duration: 0.05, gain: 0.2, filter: "highpass", freq: 5200, delay: i * 0.09 });
    }
  }

  photoFinishDrone() {
    this.tone({ freq: 330, type: "sine", duration: 2.6, gain: 0.1, detune: 8 });
    this.tone({ freq: 494, type: "sine", duration: 2.6, gain: 0.08, detune: -8 });
  }

  fanfare() {
    const notes = [0, 4, 7, 12];
    notes.forEach((semitone, index) => {
      this.tone({
        freq: 392 * Math.pow(2, semitone / 12),
        type: "square",
        duration: 0.34,
        gain: 0.22,
        delay: index * 0.13,
      });
      this.tone({
        freq: 196 * Math.pow(2, semitone / 12),
        type: "triangle",
        duration: 0.4,
        gain: 0.18,
        delay: index * 0.13,
      });
    });
    this.noise({ duration: 1.4, gain: 0.14, freq: 1100, q: 0.5, delay: 0.5 });
  }

  sadTrombone() {
    [0, -1, -2, -4].forEach((semitone, index) =>
      this.tone({
        freq: 260 * Math.pow(2, semitone / 12),
        type: "triangle",
        duration: 0.4,
        gain: 0.16,
        delay: index * 0.22,
        slideTo: 240 * Math.pow(2, (semitone - 1) / 12),
      })
    );
  }

  confettiPop() {
    this.noise({ duration: 0.12, gain: 0.24, filter: "highpass", freq: 1800 });
    this.tone({ freq: 900, type: "square", duration: 0.1, gain: 0.14, slideTo: 1600 });
  }

  join() {
    this.tone({ freq: 660, type: "sine", duration: 0.12, gain: 0.14 });
    this.tone({ freq: 990, type: "sine", duration: 0.14, gain: 0.1, delay: 0.08 });
  }

  /** Map an engine event/powerup id onto a sound, with a sensible default. */
  playFor(name, payload = {}) {
    switch (name) {
      case "cow":
        this.moo();
        break;
      case "freeze":
      case "circuit_breaker":
        this.freeze();
        break;
      case "shield":
        this.shield();
        break;
      case "ghost":
      case "ghost_horse":
        this.ghost();
        break;
      case "stumble":
      case "banana":
        this.stumble();
        break;
      case "epic":
        this.cast("epic");
        break;
      default:
        this.eventSting(payload.shake || 0);
    }
  }
}
