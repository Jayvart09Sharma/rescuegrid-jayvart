import { useEffect, useRef } from 'react'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { OrbitControls, Stars } from '@react-three/drei'
import { Bloom, ChromaticAberration, EffectComposer, Noise, Vignette } from '@react-three/postprocessing'
import { BlendFunction, type ChromaticAberrationEffect } from 'postprocessing'
import * as THREE from 'three'
import { nodePos, useStore } from '../store'
import { TWIN } from '../config'
import { ParticleCity } from './ParticleCity'
import { MeshCity } from './MeshCity'
import { Drones } from './Drones'
import { Markers } from './Markers'
import { Overlays } from './Overlays'
import { Disaster } from './Disaster'

type Controls = { target: THREE.Vector3; update: () => void; autoRotate: boolean; addEventListener: (e: string, f: () => void) => void; removeEventListener: (e: string, f: () => void) => void }

const ease = (x: number) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2)

/** Intro sweep, fly-to on Q&A signal, idle auto-orbit. */
function CameraRig() {
  const camera = useThree((s) => s.camera)
  const controls = useThree((s) => s.controls) as unknown as Controls | null
  const tween = useRef<{ from: THREE.Vector3; to: THREE.Vector3; tFrom: THREE.Vector3; tTo: THREE.Vector3; t: number; dur: number } | null>(null)
  const idle = useRef(0)

  useEffect(() => {
    camera.position.set(0, 190, 40)
    tween.current = {
      from: camera.position.clone(), to: new THREE.Vector3(52, 44, 62),
      tFrom: new THREE.Vector3(0, 0, 0), tTo: new THREE.Vector3(0, 0, 0), t: 0, dur: 4.5,
    }
  }, [camera])

  useEffect(() => {
    if (!controls) return
    const onStart = () => { idle.current = 0; tween.current = null; useStore.setState({ flyTo: null }) }
    controls.addEventListener('start', onStart)
    return () => controls.removeEventListener('start', onStart)
  }, [controls])

  useEffect(
    () =>
      useStore.subscribe((s, p) => {
        const sig = s.flyTo
        if (!sig || sig === p.flyTo || !controls) return
        const pos = nodePos(s.nodes[sig.target])
        if (!pos) return
        const tTo = new THREE.Vector3(pos[0], 2, pos[1])
        const dir = camera.position.clone().sub(controls.target).setY(0).normalize()
        if (dir.lengthSq() < 0.1) dir.set(0.6, 0, 0.8)
        const to = tTo.clone().add(dir.multiplyScalar(44)).setY(40)
        tween.current = { from: camera.position.clone(), to, tFrom: controls.target.clone(), tTo, t: 0, dur: 2.2 }
        idle.current = 0
      }),
    [camera, controls],
  )

  useFrame((_, dt) => {
    if (!controls) return
    const tw = tween.current
    if (tw) {
      tw.t += dt
      const k = ease(Math.min(1, tw.t / tw.dur))
      camera.position.lerpVectors(tw.from, tw.to, k)
      // arc upward mid-flight for a "flyover" feel
      camera.position.y += Math.sin(k * Math.PI) * 18
      controls.target.lerpVectors(tw.tFrom, tw.tTo, k)
      if (tw.t >= tw.dur) tween.current = null
    }
    idle.current += dt
    controls.autoRotate = !tw && idle.current > 14
    controls.update()
  })
  return null
}

/** Shakes the whole world on dramatic events. */
function Shaker({ children }: { children: React.ReactNode }) {
  const g = useRef<THREE.Group>(null)
  const amp = useRef(0)
  useEffect(
    () =>
      useStore.subscribe((s, p) => {
        if (s.shock.nonce !== p.shock.nonce) amp.current = s.shock.severity === 'danger' ? 0.9 : 0.4
      }),
    [],
  )
  useFrame((st, dt) => {
    amp.current *= Math.exp(-dt * 3)
    const a = amp.current
    if (g.current) {
      const t = st.clock.elapsedTime
      g.current.position.set(Math.sin(t * 57) * a, Math.sin(t * 43) * a * 0.4, Math.cos(t * 51) * a)
    }
  })
  return <group ref={g}>{children}</group>
}

function Effects() {
  const ca = useRef<ChromaticAberrationEffect>(null)
  const kick = useRef(0)
  useEffect(
    () =>
      useStore.subscribe((s, p) => {
        if (s.shock.nonce !== p.shock.nonce) kick.current = 1
      }),
    [],
  )
  useFrame((_, dt) => {
    kick.current *= Math.exp(-dt * 2.5)
    if (ca.current) ca.current.offset.set(0.0006 + kick.current * 0.006, 0.0004 + kick.current * 0.004)
  })
  // Daylight mesh twin: only status glows and scan bands should bloom, not sunlit walls.
  const mesh = TWIN === 'mesh'
  return (
    <EffectComposer multisampling={0}>
      <Bloom mipmapBlur intensity={mesh ? 0.8 : 1.35} luminanceThreshold={mesh ? 0.9 : 0.24} luminanceSmoothing={0.3} radius={0.75} />
      <ChromaticAberration ref={ca} offset={new THREE.Vector2(0.0006, 0.0004)} radialModulation={false} modulationOffset={0} />
      <Noise opacity={mesh ? 0.02 : 0.045} blendFunction={BlendFunction.OVERLAY} />
      <Vignette eskil={false} offset={0.2} darkness={mesh ? 0.35 : 0.85} />
    </EffectComposer>
  )
}

export function Scene() {
  return (
    <Canvas
      className="scene"
      shadows={TWIN === 'mesh'}
      dpr={[1, 2]}
      camera={{ fov: 42, near: 0.5, far: 800, position: [0, 190, 40] }}
      gl={{ antialias: false, powerPreference: 'high-performance' }}
      onPointerMissed={() => useStore.getState().selectNode(null)}
    >
      {TWIN === 'mesh' ? (
        <>
          <color attach="background" args={['#cfe0ef']} />
          <fog attach="fog" args={['#d7e3ec', 170, 480]} />
        </>
      ) : (
        <>
          <color attach="background" args={['#010409']} />
          <fog attach="fog" args={['#010409', 140, 320]} />
          <Stars radius={260} depth={60} count={2500} factor={3} fade speed={0.4} />
        </>
      )}
      <OrbitControls makeDefault enableDamping dampingFactor={0.08} maxPolarAngle={Math.PI * 0.46} minDistance={12} maxDistance={TWIN === 'mesh' ? 320 : 220} autoRotateSpeed={0.35} />
      <CameraRig />
      <Shaker>
        {TWIN === 'mesh' ? <MeshCity /> : <ParticleCity />}
        <Overlays ground={TWIN === 'particles'} />
        <Disaster />
        <Markers />
        <Drones />
      </Shaker>
      <Effects />
    </Canvas>
  )
}
