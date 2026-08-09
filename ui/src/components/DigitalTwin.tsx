import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { Canvas, useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import { ContactShadows, Edges, Grid, Html, Line, OrbitControls } from "@react-three/drei";
import { useReducedMotion } from "framer-motion";
import { DoubleSide, MeshStandardMaterial } from "three";
import type { Band, ComponentStatus } from "../types/api";
import { bandColor, bandInk, useChartTokens, type ChartTokens } from "../lib/chart-tokens";
import { useTheme } from "../lib/theme-context";
import { Panel } from "./primitives";

type DigitalTwinProps = {
  components: ComponentStatus[];
  machineType: string;
  selectedName: string | null;
  onSelect: (name: string) => void;
};

/* Risk colour comes from the same status tokens the 2D UI uses, so the twin
 * cannot drift from the schematic beside it. */
function bandHex(tokens: ChartTokens, band: Band) {
  return bandColor(tokens, band);
}

const CHANNEL_LABELS: Record<string, string> = {
  vibration_rms_mm_s: "Vibration RMS",
  vibration_kurtosis: "Vibration kurtosis",
  temp_bearing_c: "Bearing temperature",
  temp_motor_c: "Motor temperature",
  pressure_bar: "Pressure",
  current_a: "Motor current",
  rpm: "Shaft speed",
  ambient_temp_c: "Ambient temperature",
};

/** Scene lighting is picked per theme rather than flipped: a dark room lit for
 *  dark UI reads as a black hole on a white page, so the light variant raises
 *  the ambient floor, drops the coloured rim light, and lightens the grid. */
const SCENE_DARK = {
  background: "#12161B",
  sky: "#9BB4CC",
  ground: "#10161D",
  hemisphere: 0.6,
  ambient: 0.32,
  rim: "#3987E5",
  fill: "#7FB4F2",
  fillIntensity: 22,
  gridCell: "#1E2630",
  gridSection: "#2C3A49",
  shadow: 0.5,
} as const;

const SCENE_LIGHT = {
  background: "#EEF1F5",
  sky: "#FFFFFF",
  ground: "#C6CDD6",
  hemisphere: 1.1,
  ambient: 0.72,
  rim: "#9EC0EE",
  fill: "#FFFFFF",
  fillIntensity: 10,
  gridCell: "#D7DCE3",
  gridSection: "#BFC7D1",
  shadow: 0.28,
} as const;

const STEEL = "#3E4C5C";
const STEEL_DARK = "#2C3844";
const STEEL_LIGHT = "#55677B";
const EDGE = "#6E8299";
const WORK = "#8A6E4B";
const HALF_PI = Math.PI / 2;

/** Fixed anatomy slots. The API returns exactly five components in this order for every machine
 *  type, so geometry is keyed by index and never by free-text component name. */
const SLOTS = ["bearing", "shaft", "seal", "motor", "driven"] as const;
type Slot = (typeof SLOTS)[number];
type Vec3 = [number, number, number];
type Shape = { geometry: ReactNode; position?: Vec3; rotation?: Vec3 };

type TwinState = {
  parts: Partial<Record<Slot, ComponentStatus>>;
  active: string | null;
  selected: string | null;
  reduced: boolean;
  select: (name: string) => void;
  hover: (name: string | null) => void;
};

const TwinCtx = createContext<TwinState | null>(null);

function useTwin() {
  const state = useContext(TwinCtx);
  if (!state) throw new Error("Twin parts must render inside the twin canvas");
  return state;
}

function lift(anchor: Vec3, amount: number): Vec3 {
  return [anchor[0], anchor[1] + amount, anchor[2]];
}

/** Non-instrumented structure: frames, castings, pipework. Deliberately desaturated so the five
 *  risk-bearing components are the only coloured things in the scene. */
function Steel({ children, position, rotation, tone = STEEL, opacity = 1, edges = false }: {
  children: ReactNode;
  position?: Vec3;
  rotation?: Vec3;
  tone?: string;
  opacity?: number;
  edges?: boolean;
}) {
  const translucent = opacity < 1;
  return (
    <mesh position={position} rotation={rotation}>
      {children}
      <meshStandardMaterial color={tone} metalness={0.58} roughness={0.55} transparent={translucent} opacity={opacity} depthWrite={!translucent} side={translucent ? DoubleSide : undefined} />
      {edges ? <Edges threshold={24} color={EDGE} /> : null}
    </mesh>
  );
}

/** One risk-bearing component. Every mesh in `shapes` shares a single material so the whole
 *  sub-assembly lights, dims and pulses as one clickable object. */
function Part({ slot, shapes }: { slot: Slot; shapes: Shape[] }) {
  const twin = useTwin();
  const tokens = useChartTokens();
  const component = twin.parts[slot];
  const material = useMemo(() => new MeshStandardMaterial({ metalness: 0.34, roughness: 0.3, transparent: true }), []);
  useEffect(() => () => material.dispose(), [material]);

  const color = bandHex(tokens, component?.status ?? "NODATA");
  useEffect(() => {
    material.color.set(color);
    material.emissive.set(color);
  }, [color, material]);

  const name = component?.name ?? "";
  const isActive = Boolean(name) && twin.active === name;
  const isDimmed = Boolean(twin.active) && !isActive;
  const isPredicted = component?.predicted ?? false;

  useFrame(({ clock }) => {
    const beat = twin.reduced ? 0.46 : 0.32 + (Math.sin(clock.getElapsedTime() * 3.1) + 1) * 0.24;
    const emissive = isDimmed ? 0.02 : isActive ? 0.75 : isPredicted ? beat : 0.14;
    const opacity = isDimmed ? 0.2 : 1;
    material.emissiveIntensity += (emissive - material.emissiveIntensity) * 0.16;
    material.opacity += (opacity - material.opacity) * 0.16;
  });

  return (
    <group
      onPointerOver={(event: ThreeEvent<PointerEvent>) => { event.stopPropagation(); if (name) twin.hover(name); }}
      onPointerOut={() => twin.hover(null)}
      onClick={(event: ThreeEvent<MouseEvent>) => { event.stopPropagation(); if (name) twin.select(twin.selected === name ? "" : name); }}
    >
      {shapes.map((shape, index) => (
        <mesh key={index} material={material} position={shape.position} rotation={shape.rotation}>{shape.geometry}</mesh>
      ))}
    </group>
  );
}

function ring(radius: number, tube: number, position: Vec3, axis: "x" | "y" | "z" = "x"): Shape {
  const rotation: Vec3 = axis === "x" ? [0, HALF_PI, 0] : axis === "y" ? [HALF_PI, 0, 0] : [0, 0, 0];
  return { geometry: <torusGeometry args={[radius, tube, 8, 24]} />, position, rotation };
}

/* ------------------------------------------------------------------ centrifugal pump --------- */

function PumpModel() {
  return (
    <group>
      <Steel position={[0.15, -1.62, 0]} tone={STEEL_DARK} edges><boxGeometry args={[6.8, 0.26, 2.6]} /></Steel>
      <Steel position={[-2.15, -1.34, 0]} tone={STEEL_DARK}><boxGeometry args={[1.5, 0.3, 1.5]} /></Steel>
      <Steel position={[-0.6, -1.265, 0]} tone={STEEL_DARK}><boxGeometry args={[1.0, 0.45, 1.0]} /></Steel>
      <Steel position={[1.75, -1.14, 0]} tone={STEEL_DARK}><boxGeometry args={[2.2, 0.7, 1.8]} /></Steel>

      {/* axial suction inlet */}
      <Steel position={[-3.2, 0, 0]} rotation={[0, 0, HALF_PI]}><cylinderGeometry args={[0.4, 0.4, 1.0, 20]} /></Steel>
      <Steel position={[-3.72, 0, 0]} rotation={[0, 0, HALF_PI]} tone={STEEL_LIGHT} edges><cylinderGeometry args={[0.6, 0.6, 0.16, 20]} /></Steel>

      {/* radial discharge */}
      <Steel position={[-2.15, 1.62, 0]}><cylinderGeometry args={[0.36, 0.36, 1.0, 18]} /></Steel>
      <Steel position={[-2.15, 2.2, 0]} tone={STEEL_LIGHT} edges><cylinderGeometry args={[0.54, 0.54, 0.16, 18]} /></Steel>

      {/* volute casing, cut away so the impeller inside stays readable */}
      <Steel position={[-2.15, 0, 0]} rotation={[0, 0, HALF_PI]} tone="#8CA3BA" opacity={0.16} edges><cylinderGeometry args={[1.22, 1.22, 1.0, 28]} /></Steel>
      {/* coupling guard */}
      <Steel position={[0.3, 0, 0]} tone={STEEL_LIGHT} opacity={0.22} edges><boxGeometry args={[0.85, 0.95, 0.95]} /></Steel>
      {/* motor fan cowl */}
      <Steel position={[3.0, 0, 0]} rotation={[0, 0, HALF_PI]} tone={STEEL_DARK} edges><cylinderGeometry args={[0.62, 0.62, 0.42, 20]} /></Steel>

      <Part slot="driven" shapes={[
        { geometry: <cylinderGeometry args={[0.32, 0.32, 0.62, 18]} />, position: [-2.15, 0, 0], rotation: [0, 0, HALF_PI] },
        ...Array.from({ length: 6 }, (_, index): Shape => {
          const angle = (index / 6) * Math.PI * 2;
          return { geometry: <boxGeometry args={[0.52, 0.92, 0.07]} />, position: [-2.15, Math.cos(angle) * 0.66, Math.sin(angle) * 0.66], rotation: [angle, 0, 0] };
        }),
      ]} />
      <Part slot="seal" shapes={[
        { geometry: <cylinderGeometry args={[0.34, 0.34, 0.4, 18]} />, position: [-1.35, 0, 0], rotation: [0, 0, HALF_PI] },
        ring(0.3, 0.07, [-1.58, 0, 0]),
      ]} />
      <Part slot="bearing" shapes={[
        { geometry: <cylinderGeometry args={[0.44, 0.44, 0.8, 20]} />, position: [-0.6, 0, 0], rotation: [0, 0, HALF_PI] },
        ring(0.36, 0.1, [-0.92, 0, 0]),
        ring(0.36, 0.1, [-0.28, 0, 0]),
      ]} />
      <Part slot="shaft" shapes={[
        { geometry: <cylinderGeometry args={[0.15, 0.15, 3.3, 16]} />, position: [-0.4, 0, 0], rotation: [0, 0, HALF_PI] },
      ]} />
      <Part slot="motor" shapes={[
        { geometry: <cylinderGeometry args={[0.8, 0.8, 2.0, 26]} />, position: [1.75, 0, 0], rotation: [0, 0, HALF_PI] },
        ring(0.82, 0.06, [1.1, 0, 0]),
        ring(0.82, 0.06, [1.75, 0, 0]),
        ring(0.82, 0.06, [2.4, 0, 0]),
        { geometry: <boxGeometry args={[0.66, 0.42, 0.56]} />, position: [1.75, 0.92, 0] },
      ]} />
    </group>
  );
}

/* ------------------------------------------------------------------- hydraulic press --------- */

function PressModel() {
  return (
    <group>
      <Steel position={[-0.9, -1.9, 0]} tone={STEEL_DARK} edges><boxGeometry args={[3.5, 0.6, 2.5]} /></Steel>
      <Steel position={[-0.9, -1.44, 0]} edges><boxGeometry args={[2.9, 0.32, 2.1]} /></Steel>
      {[-1, 1].map((sx) => [-1, 1].map((sz) => (
        <Steel key={`${sx}${sz}`} position={[-0.9 + sx * 1.28, 0.25, sz * 0.9]} tone={STEEL_LIGHT}><cylinderGeometry args={[0.14, 0.14, 3.7, 14]} /></Steel>
      )))}
      <Steel position={[-0.9, 2.28, 0]} tone={STEEL_DARK} edges><boxGeometry args={[3.5, 0.66, 2.5]} /></Steel>

      {/* main cylinder, ram and platen */}
      <Steel position={[-0.9, 1.3, 0]} tone={STEEL_LIGHT} edges><cylinderGeometry args={[0.56, 0.56, 1.2, 22]} /></Steel>
      <Steel position={[-0.9, 0.1, 0]}><cylinderGeometry args={[0.3, 0.3, 1.0, 18]} /></Steel>
      <Steel position={[-0.9, -0.42, 0]} tone={STEEL_LIGHT} edges><boxGeometry args={[2.3, 0.32, 1.8]} /></Steel>
      {/* billet on the bolster, for scale */}
      <Steel position={[-0.9, -1.1, 0]} tone={WORK} edges><boxGeometry args={[1.0, 0.35, 0.9]} /></Steel>

      {/* hydraulic power unit */}
      <Steel position={[2.6, -1.05, 0]} tone={STEEL_DARK} edges><boxGeometry args={[2.0, 1.1, 1.5]} /></Steel>
      <Steel position={[2.42, -0.02, 0]} rotation={[0, 0, HALF_PI]} tone={STEEL_LIGHT}><cylinderGeometry args={[0.42, 0.42, 0.4, 18]} /></Steel>
      <Line points={[[1.42, -0.02, 0], [1.0, 0.6, 0], [0.4, 1.9, 0], [-0.35, 2.0, 0], [-0.9, 1.85, 0]]} color="#5C7085" lineWidth={2} />

      <Part slot="driven" shapes={[
        { geometry: <cylinderGeometry args={[0.34, 0.34, 0.52, 18]} />, position: [1.58, -0.02, 0], rotation: [0, 0, HALF_PI] },
        { geometry: <boxGeometry args={[0.2, 0.26, 0.24]} />, position: [1.58, 0.3, 0] },
      ]} />
      <Part slot="bearing" shapes={[
        ring(0.24, 0.08, [1.95, -0.02, 0]),
        { geometry: <boxGeometry args={[0.34, 0.44, 0.5]} />, position: [1.95, -0.28, 0] },
      ]} />
      <Part slot="shaft" shapes={[
        { geometry: <cylinderGeometry args={[0.1, 0.1, 1.0, 14]} />, position: [2.16, -0.02, 0], rotation: [0, 0, HALF_PI] },
      ]} />
      <Part slot="motor" shapes={[
        { geometry: <cylinderGeometry args={[0.46, 0.46, 1.2, 22]} />, position: [3.15, -0.02, 0], rotation: [0, 0, HALF_PI] },
        ring(0.48, 0.05, [2.75, -0.02, 0]),
        ring(0.48, 0.05, [3.15, -0.02, 0]),
        ring(0.48, 0.05, [3.55, -0.02, 0]),
        { geometry: <boxGeometry args={[0.4, 0.3, 0.36]} />, position: [3.15, 0.55, 0] },
      ]} />
      <Part slot="seal" shapes={[
        ring(0.5, 0.11, [-0.9, 0.66, 0], "y"),
        { geometry: <cylinderGeometry args={[0.44, 0.44, 0.16, 20]} />, position: [-0.9, 0.66, 0] },
      ]} />
    </group>
  );
}

/* --------------------------------------------------------------- CNC machining centre -------- */

function MillModel() {
  return (
    <group>
      <Steel position={[0.1, -2.0, 0]} tone={STEEL_DARK} edges><boxGeometry args={[4.6, 0.8, 3.0]} /></Steel>
      {/* Y saddle then X table: crossed, not stacked, so the axes read as a machine bed */}
      <Steel position={[-0.4, -1.42, 0]} edges><boxGeometry args={[2.4, 0.3, 2.2]} /></Steel>
      <Steel position={[-0.4, -1.14, 0]} tone={STEEL_LIGHT} edges><boxGeometry args={[3.6, 0.26, 1.6]} /></Steel>
      {[-0.45, 0.45].map((z) => (
        <Steel key={z} position={[-0.4, -1.0, z]} tone={STEEL_DARK}><boxGeometry args={[3.6, 0.06, 0.16]} /></Steel>
      ))}
      <Steel position={[2.1, 0.05, 0]} tone={STEEL_DARK} edges><boxGeometry args={[1.4, 3.9, 1.8]} /></Steel>
      {/* vertical way cover on the column face */}
      <Steel position={[1.32, -0.3, 0]} tone={STEEL_LIGHT} edges><boxGeometry args={[0.16, 2.4, 1.3]} /></Steel>
      <Steel position={[1.25, 0.75, 0]} edges><boxGeometry args={[1.9, 1.0, 1.2]} /></Steel>
      {/* spindle head, cut away so the shaft and bearings inside stay readable */}
      <Steel position={[0, 0.75, 0]} tone="#8CA3BA" opacity={0.16} edges><boxGeometry args={[1.5, 1.2, 1.4]} /></Steel>
      {/* workpiece clamped on the table, directly under the tool */}
      <Steel position={[0, -0.8, 0]} tone={WORK} edges><boxGeometry args={[1.15, 0.42, 0.95]} /></Steel>

      <Part slot="motor" shapes={[
        { geometry: <cylinderGeometry args={[0.44, 0.44, 0.9, 22]} />, position: [0, 1.85, 0] },
        ring(0.46, 0.05, [0, 1.6, 0], "y"),
        ring(0.46, 0.05, [0, 2.1, 0], "y"),
        { geometry: <boxGeometry args={[0.36, 0.3, 0.4]} />, position: [0, 2.42, 0] },
      ]} />
      <Part slot="shaft" shapes={[
        { geometry: <cylinderGeometry args={[0.16, 0.16, 1.5, 16]} />, position: [0, 0.85, 0] },
      ]} />
      <Part slot="bearing" shapes={[
        { geometry: <cylinderGeometry args={[0.36, 0.36, 0.7, 20]} />, position: [0, 0.4, 0] },
        ring(0.3, 0.09, [0, 0.66, 0], "y"),
        ring(0.3, 0.09, [0, 0.18, 0], "y"),
      ]} />
      <Part slot="seal" shapes={[
        { geometry: <cylinderGeometry args={[0.28, 0.28, 0.16, 18]} />, position: [0, 0.06, 0] },
        ring(0.27, 0.06, [0, 0.06, 0], "y"),
      ]} />
      <Part slot="driven" shapes={[
        { geometry: <coneGeometry args={[0.26, 0.5, 16]} />, position: [0, -0.22, 0], rotation: [Math.PI, 0, 0] },
        ring(0.27, 0.06, [0, 0.0, 0], "y"),
        { geometry: <cylinderGeometry args={[0.1, 0.1, 0.45, 14]} />, position: [0, -0.7, 0] },
      ]} />
    </group>
  );
}

/* --------------------------------------------------------------- belt conveyor drive --------- */

function ConveyorModel() {
  return (
    <group>
      {/* belt runs */}
      <Steel position={[-0.15, 0.905, 0]} tone="#1F2A35" edges><boxGeometry args={[5.5, 0.07, 1.62]} /></Steel>
      <Steel position={[-0.15, -0.065, 0]} tone="#1F2A35"><boxGeometry args={[5.5, 0.07, 1.62]} /></Steel>
      {/* tail pulley and its shaft */}
      <Steel position={[-2.9, 0.42, 0]} rotation={[HALF_PI, 0, 0]} tone={STEEL_LIGHT} edges><cylinderGeometry args={[0.45, 0.45, 1.7, 22]} /></Steel>
      <Steel position={[-2.9, 0.42, 0]} rotation={[HALF_PI, 0, 0]}><cylinderGeometry args={[0.1, 0.1, 2.2, 12]} /></Steel>
      {/* frame */}
      {[-0.95, 0.95].map((z) => (
        <Steel key={z} position={[-0.15, 0.42, z]} tone={STEEL_DARK} edges><boxGeometry args={[6.6, 0.18, 0.16]} /></Steel>
      ))}
      {[-2.3, 2.3].map((x) => [-0.95, 0.95].map((z) => (
        <Steel key={`${x}${z}`} position={[x, -0.68, z]} tone={STEEL_DARK}><cylinderGeometry args={[0.1, 0.1, 2.2, 12]} /></Steel>
      )))}
      {/* carrying idlers */}
      {[-2.0, -1.0, 0, 1.0, 2.0].map((x) => (
        <Steel key={x} position={[x, 0.67, 0]} rotation={[HALF_PI, 0, 0]}><cylinderGeometry args={[0.2, 0.2, 1.6, 14]} /></Steel>
      ))}
      {/* conveyed product, for scale and direction */}
      <Steel position={[-1.6, 1.19, 0]} tone={WORK} edges><boxGeometry args={[0.62, 0.5, 0.7]} /></Steel>
      <Steel position={[0.5, 1.19, 0]} tone={WORK} edges><boxGeometry args={[0.62, 0.5, 0.7]} /></Steel>
      {/* gearbox */}
      <Steel position={[2.6, 0.42, -1.62]} tone={STEEL_DARK} edges><boxGeometry args={[0.95, 0.95, 0.75]} /></Steel>

      <Part slot="driven" shapes={[
        { geometry: <cylinderGeometry args={[0.45, 0.45, 1.7, 24]} />, position: [2.6, 0.42, 0], rotation: [HALF_PI, 0, 0] },
        ring(0.46, 0.05, [2.6, 0.42, 0.8], "z"),
        ring(0.46, 0.05, [2.6, 0.42, -0.8], "z"),
      ]} />
      <Part slot="shaft" shapes={[
        { geometry: <cylinderGeometry args={[0.11, 0.11, 3.4, 14]} />, position: [2.6, 0.42, -0.35], rotation: [HALF_PI, 0, 0] },
      ]} />
      <Part slot="bearing" shapes={[
        ring(0.22, 0.09, [2.6, 0.42, 1.15], "z"),
        { geometry: <boxGeometry args={[0.42, 0.62, 0.34]} />, position: [2.6, 0.28, 1.12] },
      ]} />
      <Part slot="seal" shapes={[
        ring(0.2, 0.07, [2.6, 0.42, -1.18], "z"),
        { geometry: <cylinderGeometry args={[0.26, 0.26, 0.1, 18]} />, position: [2.6, 0.42, -1.18], rotation: [HALF_PI, 0, 0] },
      ]} />
      <Part slot="motor" shapes={[
        { geometry: <cylinderGeometry args={[0.4, 0.4, 1.1, 22]} />, position: [2.6, 0.42, -2.6], rotation: [HALF_PI, 0, 0] },
        ring(0.42, 0.05, [2.6, 0.42, -2.25], "z"),
        ring(0.42, 0.05, [2.6, 0.42, -2.6], "z"),
        ring(0.42, 0.05, [2.6, 0.42, -2.95], "z"),
        { geometry: <boxGeometry args={[0.34, 0.3, 0.36]} />, position: [2.6, 0.95, -2.6] },
      ]} />
    </group>
  );
}

/* ---------------------------------------------------------------------------- specs ---------- */

type TwinKind = "pump" | "press" | "mill" | "conveyor";

/** `at` sits on the part itself so a callout leader visibly starts there; `lift` is the vertical
 *  clearance needed to get the label clear of surrounding structure. */
type Anchor = { at: Vec3; lift: number };

type TwinSpec = {
  label: string;
  camera: Vec3;
  target: Vec3;
  distance: [number, number];
  anchors: Record<Slot, Anchor>;
  Model: () => ReactNode;
};

const SPECS: Record<TwinKind, TwinSpec> = {
  pump: {
    label: "Centrifugal pump",
    camera: [7.4, 3.8, 8.8],
    target: [-0.1, 0.05, 0],
    distance: [6, 18],
    anchors: {
      bearing: { at: [-0.6, 0.45, 0], lift: 1.15 },
      shaft: { at: [-0.4, 0.15, 0], lift: 1.4 },
      seal: { at: [-1.35, 0.35, 0], lift: 1.25 },
      motor: { at: [1.75, 1.15, 0], lift: 0.65 },
      driven: { at: [-2.15, 0, 1.05], lift: 1.5 },
    },
    Model: PumpModel,
  },
  press: {
    label: "Hydraulic press",
    camera: [8.0, 3.8, 9.4],
    target: [0.5, 0.2, 0],
    distance: [6, 20],
    anchors: {
      bearing: { at: [1.95, 0.22, 0], lift: 1.2 },
      shaft: { at: [2.16, 0.08, 0], lift: 1.45 },
      seal: { at: [-0.9, 0.66, 0.85], lift: 1.2 },
      motor: { at: [3.15, 0.46, 0], lift: 1.0 },
      driven: { at: [1.58, 0.32, 0], lift: 1.35 },
    },
    Model: PressModel,
  },
  mill: {
    label: "CNC machining centre",
    camera: [8.2, 3.6, 10.0],
    target: [0.3, 0.05, 0],
    distance: [6, 21],
    anchors: {
      bearing: { at: [0, 0.4, 0.75], lift: 1.55 },
      shaft: { at: [0, 0.85, 0.75], lift: 1.5 },
      seal: { at: [0, 0.06, 0.75], lift: 1.1 },
      motor: { at: [0, 2.3, 0], lift: 0.7 },
      driven: { at: [0, -0.35, 0.62], lift: 1.15 },
    },
    Model: MillModel,
  },
  conveyor: {
    label: "Belt conveyor drive",
    camera: [8.3, 4.3, 9.8],
    target: [0.6, -0.05, -0.8],
    distance: [7, 23],
    anchors: {
      bearing: { at: [2.6, 0.6, 1.15], lift: 1.35 },
      shaft: { at: [3.2, 0.42, -0.4], lift: 1.15 },
      seal: { at: [2.6, 0.42, -1.18], lift: 1.25 },
      motor: { at: [2.6, 0.82, -2.6], lift: 0.85 },
      driven: { at: [2.6, 0.87, 0], lift: 0.95 },
    },
    Model: ConveyorModel,
  },
};

function twinKind(machineType: string): TwinKind {
  const type = machineType.toLowerCase();
  if (type.includes("press")) return "press";
  if (type.includes("cnc") || type.includes("mill")) return "mill";
  if (type.includes("conv")) return "conveyor";
  return "pump";
}

/* --------------------------------------------------------------------------- scene ----------- */

function Callouts({ spec }: { spec: TwinSpec }) {
  const twin = useTwin();
  const tokens = useChartTokens();
  const activeSlot = SLOTS.find((slot) => twin.parts[slot] && twin.parts[slot]?.name === twin.active);
  const predictedSlot = SLOTS.find((slot) => twin.parts[slot]?.predicted);
  const active = activeSlot ? twin.parts[activeSlot] : undefined;
  const predicted = predictedSlot ? twin.parts[predictedSlot] : undefined;

  const predictedAnchor = predictedSlot ? spec.anchors[predictedSlot] : null;
  const activeAnchor = activeSlot ? spec.anchors[activeSlot] : null;

  return (
    <>
      {predictedAnchor && predicted && !activeSlot ? (
        <group>
          <Line points={[predictedAnchor.at, lift(predictedAnchor.at, predictedAnchor.lift)]} color="#EF4444" lineWidth={1.5} dashed dashSize={0.12} gapSize={0.09} />
          <Html position={lift(predictedAnchor.at, predictedAnchor.lift + 0.2)} center pointerEvents="none" zIndexRange={[8, 0]}>
            <span className="digital-twin-leader-label">PREDICTED FAULT</span>
          </Html>
        </group>
      ) : null}
      {activeAnchor && active ? (
        <group>
          <Line points={[activeAnchor.at, lift(activeAnchor.at, activeAnchor.lift)]} color={bandHex(tokens, active.status)} lineWidth={1.2} />
          <Html position={lift(activeAnchor.at, activeAnchor.lift + 0.45)} center pointerEvents="none" zIndexRange={[12, 0]}>
            <div className="twin-tooltip" style={{ borderColor: bandHex(tokens, active.status) }}>
              <strong>{active.name}</strong>
              <span><i style={{ background: bandHex(tokens, active.status) }} />{active.status} · risk {Math.round(active.risk)}</span>
              <small>{CHANNEL_LABELS[active.channel] ?? active.channel}</small>
            </div>
          </Html>
        </group>
      ) : null}
    </>
  );
}

function Scene({ spec, resetToken }: { spec: TwinSpec; resetToken: number }) {
  const twin = useTwin();
  const { theme } = useTheme();
  const scene = theme === "light" ? SCENE_LIGHT : SCENE_DARK;
  const controls = useThree((state) => state.controls) as { reset?: () => void } | null;
  const Model = spec.Model;

  useEffect(() => {
    if (resetToken > 0) controls?.reset?.();
  }, [resetToken, controls]);

  return (
    <>
      <color attach="background" args={[scene.background]} />
      <hemisphereLight intensity={scene.hemisphere} color={scene.sky} groundColor={scene.ground} />
      <ambientLight intensity={scene.ambient} />
      <directionalLight position={[6, 9, 6]} intensity={2.1} />
      <directionalLight position={[-7, 4, -5]} intensity={0.85} color={scene.rim} />
      <pointLight position={[0, 3, 7]} intensity={scene.fillIntensity} distance={24} color={scene.fill} />
      <Grid args={[40, 40]} position={[0, -1.94, 0]} cellSize={0.5} cellThickness={0.5} cellColor={scene.gridCell} sectionSize={2.5} sectionThickness={0.9} sectionColor={scene.gridSection} fadeDistance={32} fadeStrength={1.4} infiniteGrid />
      <ContactShadows position={[0, -1.9, 0]} opacity={scene.shadow} scale={22} blur={2.6} far={4.4} resolution={512} color="#000000" />
      <Model />
      <Callouts spec={spec} />
      <OrbitControls
        makeDefault
        target={spec.target}
        enableDamping
        dampingFactor={0.08}
        enablePan={false}
        minDistance={spec.distance[0]}
        maxDistance={spec.distance[1]}
        maxPolarAngle={Math.PI * 0.52}
        autoRotate={!twin.active && !twin.reduced}
        autoRotateSpeed={0.4}
      />
    </>
  );
}

/* --------------------------------------------------------------------------- panel ----------- */

export function DigitalTwin({ components, machineType, selectedName, onSelect }: DigitalTwinProps) {
  const tokens = useChartTokens();
  const reduced = useReducedMotion() ?? false;
  const [hovered, setHovered] = useState<string | null>(null);
  const [resetToken, setResetToken] = useState(0);
  const spec = SPECS[twinKind(machineType)];

  const parts = useMemo(() => {
    const map: Partial<Record<Slot, ComponentStatus>> = {};
    SLOTS.forEach((slot, index) => {
      const component = components[index];
      if (component) map[slot] = component;
    });
    return map;
  }, [components]);

  const active = hovered ?? selectedName;
  const state = useMemo<TwinState>(
    () => ({ parts, active, selected: selectedName, reduced, select: onSelect, hover: setHovered }),
    [parts, active, selectedName, reduced, onSelect],
  );

  const description = components.map((component) => `${component.name}: ${component.status.toLowerCase()} risk ${Math.round(component.risk)}${component.predicted ? ", predicted fault" : ""}`).join(". ");

  return (
    <Panel title="3D digital twin" className="digital-twin-panel" action={selectedName ? <button className="schematic-filter-reset" type="button" onClick={() => onSelect("")}>Show all</button> : undefined}>
      <div className="digital-twin-caption">
        <span className="eyebrow">{spec.label.toUpperCase()}</span>
        <span className="muted">Drag to orbit · click a part</span>
      </div>
      <div className={`digital-twin-canvas${hovered ? " digital-twin-canvas--pointing" : ""}`} role="img" aria-label={`Interactive 3D ${spec.label} twin. ${description}.`}>
        <Canvas camera={{ position: spec.camera, fov: 34 }} dpr={[1, 2]} gl={{ antialias: true }} onPointerMissed={() => onSelect("")}>
          <TwinCtx.Provider value={state}><Scene spec={spec} resetToken={resetToken} /></TwinCtx.Provider>
        </Canvas>
        <button className="twin-reset" type="button" onClick={() => setResetToken((token) => token + 1)}>Reset view</button>
      </div>
      <ul className="twin-parts">
        {components.map((component) => {
          const isSelected = selectedName === component.name;
          return (
            <li key={component.name}>
              <button
                type="button"
                className={`twin-part${isSelected ? " twin-part--selected" : ""}${component.predicted ? " twin-part--predicted" : ""}`}
                aria-pressed={isSelected}
                title={`${component.name} · ${CHANNEL_LABELS[component.channel] ?? component.channel}`}
                onClick={() => onSelect(isSelected ? "" : component.name)}
                onMouseEnter={() => setHovered(component.name)}
                onMouseLeave={() => setHovered(null)}
                onFocus={() => setHovered(component.name)}
                onBlur={() => setHovered(null)}
              >
                <i style={{ background: bandHex(tokens, component.status) }} aria-hidden="true" />
                <span>{component.name}</span>
                <b style={{ color: bandInk(tokens, component.status) }}>{Math.round(component.risk)}</b>
              </button>
            </li>
          );
        })}
      </ul>
      <div className="schematic-legend digital-twin-legend" aria-label="Risk band legend">{(["LOW", "MEDIUM", "HIGH", "CRITICAL"] as const).map((band) => <span key={band}><i style={{ background: bandHex(tokens, band) }} />{band}</span>)}</div>
      <p className="schematic-selection" aria-live="polite">{selectedName ? `Telemetry filtered to ${selectedName}.` : "All sensor channels shown."}</p>
    </Panel>
  );
}
