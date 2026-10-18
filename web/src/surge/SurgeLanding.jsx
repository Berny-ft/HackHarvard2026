import { useEffect, useRef, useState } from "react";
import SurgeField from "./SurgeField.jsx";
import { ChalkStack, EvalConsole, Findings, Household, Pipeline } from "./sections.jsx";
import { SurgeLogo } from "./common.jsx";
import { Brand, hrs, navigate, pct } from "../shared.jsx";
import "../landing/landing.css";
import "./surge.css";

export default function SurgeLanding() {
  const [replay, setReplay] = useState(null);
  const readout = useRef(null);

  useEffect(() => {
    document.title = "Trace — storm surge warnings, tested on four hurricanes";
    fetch("/data/surge/sentinel/replay.json").then((r) => (r.ok ? r.json() : null)).then(setReplay).catch(() => setReplay(null));
  }, []);
  useEffect(() => {
    if (replay && window.location.hash) document.querySelector(window.location.hash)?.scrollIntoView();
  }, [replay]);

  const all = replay?.summary?.rules?.all;
  const held = replay?.summary?.rules?.held_out;

  return (
    <div className="landing surge">
      <Nav />
      <header className="hero">
        <SurgeField readoutRef={readout} />
        <div className="hero-shade" />
        <div className="wrap hero-inner">
          <div className="hero-copy">
            <h1>Leave before the sea reaches your street.</h1>
            <p className="lede">
              Trace Sentinel reads every NHC hurricane forecast, the tide gauges from Miami to Maine and the
              height of the ground under each home, and tells each household when to get ready and when to go.
              We replayed Sandy, Matthew, Irma and Isabel hour by hour to see if it would have worked.
            </p>
            <div className="hero-actions">
              <a className="btn btn-primary" href="/surge/map" onClick={navigate}>Open the surge map</a>
              <a className="btn btn-ghost" href="#evaluation">Read the evaluation</a>
            </div>
          </div>
          <Readout api={readout} />
        </div>
      </header>

      <section className="proof">
        <div className="wrap proof-grid">
          <Fact value={all ? pct(all.evacuate_in_time) : "–"} label="of sampled homes that flooded were told to leave before the water arrived" />
          <Fact value={all ? hrs(all.median_evacuate_lead_h) : "–"} label="median head start on the order to leave" />
          <Fact value={held ? pct(held.evacuate_12h_ahead) : "–"} label="told to leave 12 hours or more ahead on Irma and Isabel, which the agent was never tuned on" />
          <Fact value={all ? pct(all.needless_evacuation_rate) : "–"} label="of low-lying homes that stayed dry were told to evacuate anyway. We count those too." />
        </div>
      </section>

      <Section id="evaluation" title="Graded against what the tide gauges saw"
               intro="Every household is replayed hour by hour with only the NHC advisories issued by then. When the water arrived comes from NOAA tide gauges and USGS elevation, not from our surge model, so the agent can't grade its own homework.">
        <EvalConsole replay={replay} />
      </Section>

      <Section id="household" title="What one household received"
               intro="A home near New York Harbor that Sandy's surge reached. These are the messages the address-rules policy sent, at the hours it sent them, from a forecast made days ahead.">
        <Household replay={replay} />
      </Section>

      <Section id="how" title="How Sentinel decides"
               intro="Generic warnings go out by zone and category. Sentinel asks a narrower question for every subscriber: will seawater reach this address, how deep, and when?">
        <Pipeline />
      </Section>

      <Section id="findings" title="What the replays taught us"
               intro="Not tuned to look good. Thresholds were fitted on Sandy and Matthew only; Irma and Isabel are held out.">
        <Findings replay={replay} />
      </Section>

      <Section id="chalk" title="Built on Chalk"
               intro="Chalk routes the agent's model calls, scores every policy against the same replay dataset, and runs the loop where the agent learns from storms it got wrong.">
        <ChalkStack />
      </Section>

      <footer className="closing">
        <div className="wrap closing-inner">
          <h2>Watch your own address through a past storm.</h2>
          <p>Pick any spot on the map, press play, and get the alerts the sentinel would have sent, by email, hour by hour.</p>
          <a className="btn btn-primary" href="/surge/map" onClick={navigate}>Open the surge map</a>
        </div>
        <div className="wrap colophon">
          <SurgeLogo />
          <p>
            Data: NHC best tracks and official forecasts (ATCF), NOAA CO-OPS tide gauges, USGS 3DEP elevation, NWS alerts,
            Open-Meteo. Built at HackHarvard 2026, alongside <a href="/" onClick={navigate}>wildfire warnings</a>. Not an official
            warning service: always follow orders from local authorities.
          </p>
        </div>
      </footer>
    </div>
  );
}

function Nav() {
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const on = () => setScrolled(window.scrollY > 24);
    on();
    window.addEventListener("scroll", on, { passive: true });
    return () => window.removeEventListener("scroll", on);
  }, []);
  return (
    <nav className={`nav ${scrolled ? "is-scrolled" : ""}`}>
      <div className="wrap nav-inner">
        <Brand mode="water" />
        <div className="nav-links">
          <a href="#evaluation">Evaluation</a>
          <a href="#how">How it works</a>
          <a href="#chalk">Chalk</a>
        </div>
        <a className="btn btn-small" href="/surge/map" onClick={navigate}>Open map</a>
      </div>
    </nav>
  );
}

function Readout({ api }) {
  const nodes = useRef({});
  useEffect(() => { api.current = nodes.current; }, [api]);
  const set = (k) => (el) => { nodes.current[k] = el; };
  return (
    <figure className="readout" aria-label="Hurricane Sandy replay">
      <figcaption>Hurricane Sandy, New York Harbor</figcaption>
      <dl>
        <div><dt>Local time</dt><dd className="num" ref={set("time")}>Oct 29, 6:00 PM</dd></div>
        <div><dt>From the peak</dt><dd className="num" ref={set("hour")}>−0h</dd></div>
        <div><dt>Water at The Battery</dt><dd className="num" ref={set("water")}>–</dd></div>
        <div><dt>Acres under water</dt><dd className="num" ref={set("acres")}>0</dd></div>
        <div><dt>Storm centre</dt><dd className="num" ref={set("storm")}>–</dd></div>
      </dl>
      <div className="readout-key">
        <span><i className="k-sea">~</i>Sea</span>
        <span><i className="k-low">o</i>Will flood</span>
        <span><i className="k-wet">≈</i>Under water</span>
      </div>
    </figure>
  );
}

function Fact({ value, label }) {
  return <div className="fact"><div className="fact-value num">{value}</div><p>{label}</p></div>;
}

function Section({ id, title, intro, children }) {
  return (
    <section id={id} className="section">
      <div className="wrap section-grid">
        <div className="section-head"><h2>{title}</h2><p>{intro}</p></div>
        <div className="section-body">{children}</div>
      </div>
    </section>
  );
}
