import { useEffect, useRef, useState } from "react";
import HeroField from "./HeroField.jsx";
import { ChalkStack, EvalConsole, Findings, Household, Pipeline } from "./sections.jsx";
import { Logo, hrs, navigate, pct } from "../shared.jsx";
import "./landing.css";


export default function Landing() {
  const [replay, setReplay] = useState(null);
  const readout = useRef(null);

  useEffect(() => {
    document.title = "FireTrace — wildfire warnings, tested on Eaton and Palisades";
    fetch("/data/sentinel/replay.json").then((r) => (r.ok ? r.json() : null)).then(setReplay).catch(() => setReplay(null));
  }, []);

  // Honour /#evaluation and friends once the sections have their data and height.
  useEffect(() => {
    if (replay && window.location.hash) document.querySelector(window.location.hash)?.scrollIntoView();
  }, [replay]);

  const headline = replay?.summary?.rules?.all;

  return (
    <div className="landing">
      <Nav />

      <header className="hero">
        <HeroField readoutRef={readout} />
        <div className="hero-shade" />
        <div className="wrap hero-inner">
          <div className="hero-copy">
            <h1>Leave before the fire reaches your street.</h1>
            <p className="lede">
              FireTrace Sentinel watches satellites, wind and fuel across Los Angeles and Ventura
              counties, and tells each household when to get ready and when to go. We replayed the
              Eaton and Palisades fires hour by hour to see if it would have worked.
            </p>
            <div className="hero-actions">
              <a className="btn btn-primary" href="/map" onClick={navigate}>Open the live map</a>
              <a className="btn btn-ghost" href="#evaluation">Read the evaluation</a>
            </div>
          </div>
          <Readout api={readout} />
        </div>
      </header>

      <section className="proof">
        <div className="wrap proof-grid">
          <Fact value={headline ? pct(headline.warned_in_time) : "100%"}
                label="of sampled destroyed homes were warned before satellites saw fire arrive" />
          <Fact value={headline ? hrs(headline.median_warn_lead_h) : "8.7 h"}
                label="median head start on the warning" />
          <Fact value={replay ? replay.households.length.toString() : "600"}
                label="households replayed across two fires, including 200 the fire never reached" />
          <Fact value={headline ? pct(headline.over_evacuation_rate) : "4%"}
                label="of households outside the burn area were told to evacuate. We count those too." />
        </div>
      </section>

      <Section id="evaluation" title="Graded against what satellites saw"
               intro="Every household is replayed hour by hour with only the information available at that hour. The fire's arrival comes from the first satellite detection within 750 m, not from our own model, so the agent can't grade its own homework.">
        <EvalConsole replay={replay} />
      </Section>

      <Section id="household" title="What one household received"
               intro="A home on the west side of the Eaton Fire, later recorded destroyed by CAL FIRE. These are the messages the wind-rules policy sent, at the hours it sent them.">
        <Household replay={replay} />
      </Section>

      <Section id="how" title="How Sentinel decides"
               intro="Four steps run every fifteen minutes for every subscriber. Messages go to people by text and to their personal agents as signed webhooks.">
        <Pipeline />
      </Section>

      <Section id="findings" title="What the replays taught us"
               intro="The numbers above aren't tuned to look good. These are the results that changed how the agent works.">
        <Findings replay={replay} />
      </Section>

      <Section id="chalk" title="Built on Chalk"
               intro="Chalk runs the agent, routes its model calls and scores every policy change against the same replay dataset.">
        <ChalkStack />
      </Section>

      <footer className="closing">
        <div className="wrap closing-inner">
          <h2>See what the agent sees right now.</h2>
          <p>Live Red Flag Warnings, Santa Ana forecasts, satellite hot spots and active fires, with both 2025 fires available to replay on the map.</p>
          <a className="btn btn-primary" href="/map" onClick={navigate}>Open the live map</a>
        </div>
        <div className="wrap colophon">
          <Logo />
          <p>
            Data: NASA FIRMS (VIIRS, MODIS), NIFC, NWS, CAL FIRE FRAP and DINS, LANDFIRE, USGS 3DEP, Open-Meteo,
            OpenStreetMap. Built at HackHarvard 2026. Not an official evacuation service: always follow orders from local authorities.
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
        <a href="/" className="nav-logo" aria-label="FireTrace home"><Logo /></a>
        <div className="nav-links">
          <a href="#evaluation">Evaluation</a>
          <a href="#how">How it works</a>
          <a href="#chalk">Chalk</a>
        </div>
        <a className="btn btn-small" href="/map" onClick={navigate}>Open map</a>
      </div>
    </nav>
  );
}

// Live caption for the hero field; HeroField writes into these nodes every frame.
function Readout({ api }) {
  const nodes = useRef({});
  useEffect(() => { api.current = nodes.current; }, [api]);
  const set = (k) => (el) => { nodes.current[k] = el; };
  return (
    <figure className="readout" aria-label="Eaton Fire replay">
      <figcaption>Eaton Fire, satellite detections</figcaption>
      <dl>
        <div><dt>Local time</dt><dd className="num" ref={set("time")}>Jan 7, 6:00 PM</dd></div>
        <div><dt>Since ignition</dt><dd className="num" ref={set("hour")}>T+0h</dd></div>
        <div><dt>Acres seen burning</dt><dd className="num" ref={set("acres")}>0</dd></div>
        <div>
          <dt>Wind</dt>
          <dd className="num"><span className="wind-arrow" ref={set("arrow")} aria-hidden="true">↑</span><span ref={set("wind")}>–</span></dd>
        </div>
      </dl>
      <div className="readout-key">
        <span><i className="k-town">.</i>Homes</span>
        <span><i className="k-fire">*</i>Burning</span>
        <span><i className="k-lost">x</i>Destroyed</span>
      </div>
    </figure>
  );
}

function Fact({ value, label }) {
  return (
    <div className="fact">
      <div className="fact-value num">{value}</div>
      <p>{label}</p>
    </div>
  );
}

function Section({ id, title, intro, children }) {
  return (
    <section id={id} className="section">
      <div className="wrap section-grid">
        <div className="section-head">
          <h2>{title}</h2>
          <p>{intro}</p>
        </div>
        <div className="section-body">{children}</div>
      </div>
    </section>
  );
}
