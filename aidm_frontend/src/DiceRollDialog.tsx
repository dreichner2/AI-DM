import { useEffect, useRef, useState } from 'react'
import { X } from 'lucide-react'
import {
  Body,
  ContactMaterial,
  ConvexPolyhedron,
  Material as CannonMaterial,
  Plane,
  Quaternion as CannonQuaternion,
  Vec3,
  World,
} from 'cannon-es'
import {
  AmbientLight,
  Box3,
  BoxGeometry,
  BufferGeometry,
  CanvasTexture,
  DirectionalLight,
  DodecahedronGeometry,
  DoubleSide,
  EdgesGeometry,
  Float32BufferAttribute,
  Group,
  IcosahedronGeometry,
  LineBasicMaterial,
  LineSegments,
  Mesh,
  MeshBasicMaterial,
  MeshStandardMaterial,
  OctahedronGeometry,
  PerspectiveCamera,
  PlaneGeometry,
  Quaternion,
  RingGeometry,
  Scene,
  SRGBColorSpace,
  TetrahedronGeometry,
  Vector3,
  WebGLRenderer,
  type Material,
  type Object3D,
  type Texture,
} from 'three'

type DiceRollStatus = 'rolling' | 'sending'

type DiceRollDialogProps = {
  die: string
  result: number
  modifier: number
  total: number
  targetLabel?: string | null
  rollKey: number
  status: DiceRollStatus
  onCancel: () => void
  onComplete: () => void
}

const ROLL_DURATION_MS = 900
const PHYSICS_ROLL_DURATION_MS = 1050
const RESULT_HOLD_MS = 550
const FACE_LABEL_DEPTH_OFFSET = 0.045
const FRONT_NORMAL = new Vector3(0, 0, 1)
const TABLE_SURFACE_Y = -1.12
const TABLE_VISUAL_SINK = 0.08
const DICE_VISUAL_SCALE = 0.58
const TABLE_GROUND_PADDING = 0.012
const MAX_PHYSICS_VISUAL_HOP = 0.035
const DICE_ANIMATION_STORAGE_KEY = 'aidm:diceAnimationEngine'
const DEFAULT_DICE_ANIMATION_ENGINE = 'scripted'
const PHYSICS_STEP_SECONDS = 1 / 90
const PHYSICS_MAX_SUBSTEPS = 4

type DiceAnimationEngine = 'scripted' | 'physics'

type DiceCanvasProps = {
  die: string
  result: number
  rollKey: number
  onLanded: () => void
  onComplete: () => void
}

const DIE_SIDES: Record<string, number> = {
  d4: 4,
  d6: 6,
  d8: 8,
  d10: 10,
  d12: 12,
  d20: 20,
  d100: 100,
}

type FaceSample = {
  center: Vector3
  normal: Vector3
}

type DiceAnimationProfile = {
  bounce: number
  settle: number
  spin: number
  travel: number
}

function easeOutCubic(value: number) {
  return 1 - (1 - value) ** 3
}

function easeInOutCubic(value: number) {
  return value < 0.5 ? 4 * value ** 3 : 1 - (-2 * value + 2) ** 3 / 2
}

function clamp01(value: number) {
  return Math.min(Math.max(value, 0), 1)
}

function isDiceAnimationEngine(value: string | null | undefined): value is DiceAnimationEngine {
  return value === 'scripted' || value === 'physics'
}

function getConfiguredDiceAnimationEngine(): DiceAnimationEngine {
  if (typeof window !== 'undefined') {
    try {
      const storedEngine = window.localStorage.getItem(DICE_ANIMATION_STORAGE_KEY)
      if (isDiceAnimationEngine(storedEngine)) return storedEngine
    } catch {
      // Ignore storage failures; the env/default path still works.
    }
  }

  const envEngine = (
    import.meta.env.VITE_AIDM_DICE_ANIMATION_ENGINE
    ?? import.meta.env.VITE_AIDM_DICE_ANIMATION
  ) as string | undefined
  return isDiceAnimationEngine(envEngine) ? envEngine : DEFAULT_DICE_ANIMATION_ENGINE
}

function prefersReducedMotion() {
  return typeof window !== 'undefined'
    ? window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
    : false
}

function seededNoise(seed: number) {
  const value = Math.sin(seed * 12.9898) * 43758.5453
  return value - Math.floor(value)
}

function seededRange(seed: number, min: number, max: number) {
  return min + seededNoise(seed) * (max - min)
}

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max)
}

function createResultRestQuaternion(resultNormal: Vector3, seed: number) {
  const readableRestNormal = new Vector3(
    seededRange(seed + 41, -0.16, 0.12),
    seededRange(seed + 42, 0.55, 0.65),
    0.82,
  ).normalize()
  const base = new Quaternion().setFromUnitVectors(resultNormal.clone().normalize(), readableRestNormal)
  const twist = new Quaternion().setFromAxisAngle(readableRestNormal, seededRange(seed + 43, -0.22, 0.22))
  return twist.multiply(base).normalize()
}

function placeDiceOnTable(
  dice: Group,
  bodyMesh: Mesh,
  bounds: Box3,
  visualLift = 0,
  visualSink = 0,
) {
  dice.updateMatrixWorld(true)
  bounds.setFromObject(bodyMesh)
  dice.position.y += TABLE_SURFACE_Y - bounds.min.y + TABLE_GROUND_PADDING - visualSink + visualLift
}

function animationProfileForDie(die: string): DiceAnimationProfile {
  const normalizedDie = die.toLowerCase()
  if (normalizedDie === 'd4') return { bounce: 0.46, settle: 1.18, spin: 0.5, travel: 0.38 }
  if (normalizedDie === 'd6') return { bounce: 0.52, settle: 1.08, spin: 0.54, travel: 0.42 }
  if (normalizedDie === 'd8') return { bounce: 0.56, settle: 1.02, spin: 0.58, travel: 0.44 }
  if (normalizedDie === 'd10') return { bounce: 0.54, settle: 1, spin: 0.6, travel: 0.46 }
  if (normalizedDie === 'd12') return { bounce: 0.58, settle: 0.96, spin: 0.62, travel: 0.48 }
  if (normalizedDie === 'd100') return { bounce: 0.54, settle: 0.94, spin: 0.58, travel: 0.44 }
  return { bounce: 0.6, settle: 0.9, spin: 0.64, travel: 0.5 }
}

function signedModifier(value: number) {
  if (!value) return ''
  return value > 0 ? `+${value}` : String(value)
}

function createBipyramidGeometry(points: number) {
  const radius = 1.08
  const height = 1.32
  const vertices: number[] = []
  const ring = Array.from({ length: points }, (_, index) => {
    const angle = (index / points) * Math.PI * 2 + Math.PI / points
    return new Vector3(Math.cos(angle) * radius, 0, Math.sin(angle) * radius)
  })
  const top = new Vector3(0, height, 0)
  const bottom = new Vector3(0, -height, 0)

  for (let index = 0; index < points; index += 1) {
    const nextIndex = (index + 1) % points
    vertices.push(top.x, top.y, top.z, ring[index].x, ring[index].y, ring[index].z, ring[nextIndex].x, ring[nextIndex].y, ring[nextIndex].z)
    vertices.push(
      bottom.x,
      bottom.y,
      bottom.z,
      ring[nextIndex].x,
      ring[nextIndex].y,
      ring[nextIndex].z,
      ring[index].x,
      ring[index].y,
      ring[index].z,
    )
  }

  const geometry = new BufferGeometry()
  geometry.setAttribute('position', new Float32BufferAttribute(vertices, 3))
  geometry.computeVertexNormals()
  return geometry
}

function createGeometryForDie(die: string) {
  const normalized = die.toLowerCase()
  if (normalized === 'd4') return new TetrahedronGeometry(1.35)
  if (normalized === 'd6') return new BoxGeometry(1.72, 1.72, 1.72)
  if (normalized === 'd8') return new OctahedronGeometry(1.42)
  if (normalized === 'd10') return createBipyramidGeometry(5)
  if (normalized === 'd12') return new DodecahedronGeometry(1.34)
  if (normalized === 'd100') return createBipyramidGeometry(10)
  return new IcosahedronGeometry(1.38)
}

function getDieSides(die: string) {
  return DIE_SIDES[die.toLowerCase()] ?? 20
}

function createCubeFaceSamples(): FaceSample[] {
  const offset = 0.88
  return [
    { center: new Vector3(0, 0, offset), normal: new Vector3(0, 0, 1) },
    { center: new Vector3(offset, 0, 0), normal: new Vector3(1, 0, 0) },
    { center: new Vector3(-offset, 0, 0), normal: new Vector3(-1, 0, 0) },
    { center: new Vector3(0, offset, 0), normal: new Vector3(0, 1, 0) },
    { center: new Vector3(0, -offset, 0), normal: new Vector3(0, -1, 0) },
    { center: new Vector3(0, 0, -offset), normal: new Vector3(0, 0, -1) },
  ]
}

function createGeometryFaceSamples(geometry: BufferGeometry): FaceSample[] {
  const source = geometry.index ? geometry.toNonIndexed() : geometry
  const position = source.getAttribute('position')
  const samples: FaceSample[] = []

  for (let index = 0; index < position.count; index += 3) {
    const a = new Vector3().fromBufferAttribute(position, index)
    const b = new Vector3().fromBufferAttribute(position, index + 1)
    const c = new Vector3().fromBufferAttribute(position, index + 2)
    const normal = new Vector3().subVectors(b, a).cross(new Vector3().subVectors(c, a)).normalize()
    if (!Number.isFinite(normal.lengthSq()) || normal.lengthSq() === 0) continue
    const center = new Vector3().addVectors(a, b).add(c).multiplyScalar(1 / 3)
    samples.push({ center, normal })
  }

  if (source !== geometry) {
    source.dispose()
  }
  return samples
}

function createFaceSamples(die: string, geometry: BufferGeometry) {
  return die.toLowerCase() === 'd6' ? createCubeFaceSamples() : createGeometryFaceSamples(geometry)
}

function pickEvenly<T>(items: T[], count: number) {
  if (count >= items.length) return items
  return Array.from({ length: count }, (_, index) => {
    const itemIndex = Math.floor((index / count) * items.length)
    return items[itemIndex]
  })
}

function sideLabelValues(die: string, result: number, count: number) {
  const sides = getDieSides(die)
  const pool = Array.from({ length: sides }, (_, index) => index + 1).filter((value) => value !== result)
  const start = Math.abs(result + count) % Math.max(pool.length, 1)
  return Array.from({ length: count }, (_, index) => pool[(start + index * 3) % pool.length] ?? index + 1)
}

function createFaceLabelTexture(label: string, isResult = false) {
  const canvas = document.createElement('canvas')
  canvas.width = 256
  canvas.height = 256
  const context = canvas.getContext('2d')
  if (context) {
    context.clearRect(0, 0, canvas.width, canvas.height)
    const center = canvas.width / 2
    const badgeRadius = isResult ? 88 : 72
    const fontSize = label.length >= 3 ? (isResult ? 80 : 56) : label.length === 2 ? (isResult ? 98 : 68) : isResult ? 120 : 82

    context.fillStyle = isResult ? 'rgba(8, 16, 16, 0.88)' : 'rgba(8, 16, 16, 0.68)'
    context.strokeStyle = isResult ? 'rgba(255, 218, 166, 0.92)' : 'rgba(255, 218, 166, 0.72)'
    context.lineWidth = isResult ? 9 : 6
    context.beginPath()
    context.arc(center, center, badgeRadius, 0, Math.PI * 2)
    context.fill()
    context.stroke()
    context.shadowColor = 'rgba(0, 0, 0, 0.58)'
    context.shadowBlur = 8
    context.shadowOffsetY = 3
    context.fillStyle = '#fff3df'
    context.font = `800 ${fontSize}px Inter, system-ui, sans-serif`
    context.textAlign = 'center'
    context.textBaseline = 'middle'
    context.fillText(label, center, center + (label.length >= 3 ? 3 : 2))
  }

  const texture = new CanvasTexture(canvas)
  texture.colorSpace = SRGBColorSpace
  return texture
}

function createContactShadowTexture() {
  const canvas = document.createElement('canvas')
  canvas.width = 256
  canvas.height = 256
  const context = canvas.getContext('2d')
  if (context) {
    const gradient = context.createRadialGradient(128, 128, 8, 128, 128, 118)
    gradient.addColorStop(0, 'rgba(0, 0, 0, 0.68)')
    gradient.addColorStop(0.42, 'rgba(0, 0, 0, 0.34)')
    gradient.addColorStop(1, 'rgba(0, 0, 0, 0)')
    context.fillStyle = gradient
    context.fillRect(0, 0, canvas.width, canvas.height)
  }

  const texture = new CanvasTexture(canvas)
  texture.colorSpace = SRGBColorSpace
  return texture
}

function createNumberPlane(label: string, sample: FaceSample, isResult = false) {
  const labelScale = isResult ? (label.length >= 3 ? 0.94 : 0.78) : label.length >= 3 ? 0.54 : 0.46
  const texture = createFaceLabelTexture(label, isResult)
  const material = new MeshBasicMaterial({
    map: texture,
    transparent: true,
    opacity: isResult ? 0 : 1,
    side: DoubleSide,
    depthWrite: false,
  })
  const plane = new Mesh(new PlaneGeometry(labelScale, labelScale), material)
  plane.position.copy(sample.center).addScaledVector(sample.normal, FACE_LABEL_DEPTH_OFFSET)
  plane.quaternion.setFromUnitVectors(FRONT_NORMAL, sample.normal)
  return plane
}

function createResultHalo(label: string, sample: FaceSample) {
  const outerRadius = label.length >= 3 ? 0.58 : 0.5
  const material = new MeshBasicMaterial({
    color: 0xffd6a0,
    transparent: true,
    opacity: 0,
    side: DoubleSide,
    depthWrite: false,
  })
  const halo = new Mesh(new RingGeometry(outerRadius - 0.045, outerRadius, 72), material)
  halo.position.copy(sample.center).addScaledVector(sample.normal, FACE_LABEL_DEPTH_OFFSET + 0.018)
  halo.quaternion.setFromUnitVectors(FRONT_NORMAL, sample.normal)
  halo.visible = false
  return halo
}

function disposeMaterial(material: Material) {
  const mapped = material as Material & { map?: Texture }
  mapped.map?.dispose()
  material.dispose()
}

function disposeObject(object: Object3D) {
  object.traverse((child) => {
    const mesh = child as Mesh
    mesh.geometry?.dispose()
    const material = mesh.material
    if (Array.isArray(material)) {
      material.forEach(disposeMaterial)
    } else if (material) {
      disposeMaterial(material)
    }
  })
}

function createDiceGroup(die: string, result: number) {
  const normalizedDie = die.toLowerCase()
  const geometry = createGeometryForDie(die)
  const faceSamples = createFaceSamples(normalizedDie, geometry)
  const frontFace = faceSamples.reduce((best, sample) => (sample.normal.z > best.normal.z ? sample : best), faceSamples[0])
  const resultNormal = frontFace.normal.clone()
  const sideFaces = faceSamples.filter((sample) => sample !== frontFace && sample.normal.dot(frontFace.normal) < 0.96)
  const sideLabels = pickEvenly(sideFaces, Math.min(getDieSides(normalizedDie) - 1, 22, sideFaces.length))
  const sideValues = sideLabelValues(normalizedDie, result, sideLabels.length)
  const group = new Group()
  const mesh = new Mesh(
    geometry,
    new MeshStandardMaterial({
      color: 0xc64f22,
      emissive: 0x2a0d04,
      metalness: 0.18,
      roughness: 0.54,
      flatShading: true,
    }),
  )
  const edgeMaterial = new LineBasicMaterial({
    color: 0xffd19a,
    transparent: true,
    opacity: 0.64,
  })
  const edges = new LineSegments(new EdgesGeometry(geometry), edgeMaterial)
  const resultFace = createNumberPlane(String(result), frontFace, true)
  const resultHalo = createResultHalo(String(result), frontFace)
  const sideLabelPlanes: Mesh[] = []
  resultFace.visible = false

  mesh.castShadow = false
  mesh.receiveShadow = true
  group.add(mesh, edges)
  sideLabels.forEach((sample, index) => {
    const labelPlane = createNumberPlane(String(sideValues[index]), sample)
    sideLabelPlanes.push(labelPlane)
    group.add(labelPlane)
  })
  group.add(resultHalo, resultFace)
  return { group, bodyMesh: mesh, resultFace, resultHalo, resultNormal, sideLabelPlanes }
}

function createDiceCollider(geometry: BufferGeometry, scale = DICE_VISUAL_SCALE) {
  const source = geometry.index ? geometry.toNonIndexed() : geometry
  const position = source.getAttribute('position')
  const vertexMap = new Map<string, number>()
  const vertices: Vec3[] = []
  const faces: number[][] = []
  let minY = Infinity
  let maxY = -Infinity

  for (let index = 0; index < position.count; index += 3) {
    const face: number[] = []
    for (let offset = 0; offset < 3; offset += 1) {
      const vertexIndex = index + offset
      const x = position.getX(vertexIndex) * scale
      const y = position.getY(vertexIndex) * scale
      const z = position.getZ(vertexIndex) * scale
      const key = `${x.toFixed(4)}:${y.toFixed(4)}:${z.toFixed(4)}`
      let mappedIndex = vertexMap.get(key)

      if (mappedIndex === undefined) {
        mappedIndex = vertices.length
        vertexMap.set(key, mappedIndex)
        vertices.push(new Vec3(x, y, z))
        minY = Math.min(minY, y)
        maxY = Math.max(maxY, y)
      }

      face.push(mappedIndex)
    }

    if (new Set(face).size === 3) {
      faces.push(face)
    }
  }

  if (source !== geometry) {
    source.dispose()
  }

  const shape = new ConvexPolyhedron({ vertices, faces })
  shape.computeNormals()
  shape.computeEdges()
  shape.updateBoundingSphereRadius()
  return { shape, halfHeight: Math.max(Math.abs(minY), Math.abs(maxY), 0.78) }
}

function createDiceStage(mount: HTMLDivElement) {
  const renderer = new WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
  renderer.shadowMap.enabled = true
  renderer.outputColorSpace = SRGBColorSpace
  renderer.domElement.setAttribute('aria-hidden', 'true')
  mount.appendChild(renderer.domElement)

  const scene = new Scene()
  const camera = new PerspectiveCamera(38, 1, 0.1, 100)
  camera.position.set(0, 0.35, 5.6)

  const keyLight = new DirectionalLight(0xffdfbd, 2.5)
  keyLight.position.set(3.2, 4.4, 4.2)
  keyLight.castShadow = false
  keyLight.shadow.mapSize.width = 1024
  keyLight.shadow.mapSize.height = 1024
  keyLight.shadow.camera.left = -3.4
  keyLight.shadow.camera.right = 3.4
  keyLight.shadow.camera.top = 2.4
  keyLight.shadow.camera.bottom = -2.4
  keyLight.shadow.camera.near = 0.2
  keyLight.shadow.camera.far = 12
  const rimLight = new DirectionalLight(0x72b59b, 1.15)
  rimLight.position.set(-4, 1.2, -2.4)
  scene.add(new AmbientLight(0xffffff, 0.58), keyLight, rimLight)

  const table = new Mesh(
    new PlaneGeometry(6.4, 3.2),
    new MeshStandardMaterial({
      color: 0x182526,
      roughness: 0.82,
      metalness: 0.02,
    }),
  )
  table.position.set(0, TABLE_SURFACE_Y, -0.28)
  table.rotation.x = -Math.PI / 2
  table.receiveShadow = true
  scene.add(table)

  const contactShadowMaterial = new MeshBasicMaterial({
    map: createContactShadowTexture(),
    transparent: true,
    opacity: 0.3,
    side: DoubleSide,
    depthWrite: false,
  })
  const contactShadow = new Mesh(new PlaneGeometry(1.6, 0.86), contactShadowMaterial)
  contactShadow.position.set(0, TABLE_SURFACE_Y + 0.026, -0.2)
  contactShadow.rotation.x = -Math.PI / 2
  scene.add(contactShadow)

  const render = () => renderer.render(scene, camera)
  const resize = () => {
    const width = Math.max(240, mount.clientWidth)
    const height = Math.max(220, mount.clientHeight)
    renderer.setSize(width, height, false)
    camera.aspect = width / height
    camera.updateProjectionMatrix()
    render()
  }
  const observer =
    typeof ResizeObserver !== 'undefined'
      ? new ResizeObserver(() => resize())
      : null
  observer?.observe(mount)
  window.addEventListener('resize', resize)
  resize()

  return {
    camera,
    contactShadow,
    contactShadowMaterial,
    render,
    scene,
    dispose: () => {
      window.removeEventListener('resize', resize)
      observer?.disconnect()
      disposeObject(scene)
      renderer.dispose()
      renderer.domElement.remove()
    },
  }
}

function ScriptedDiceCanvas({
  die,
  result,
  rollKey,
  onLanded,
  onComplete,
}: DiceCanvasProps) {
  const mountRef = useRef<HTMLDivElement | null>(null)
  const completeRef = useRef(onComplete)
  const landedRef = useRef(onLanded)

  useEffect(() => {
    completeRef.current = onComplete
  }, [onComplete])

  useEffect(() => {
    landedRef.current = onLanded
  }, [onLanded])

  useEffect(() => {
    const mount = mountRef.current
    if (!mount) return undefined

    const { camera, contactShadow, contactShadowMaterial, render, scene, dispose } = createDiceStage(mount)

    const { group: dice, bodyMesh, resultFace, resultHalo, sideLabelPlanes } = createDiceGroup(die, result)
    dice.rotation.set(-0.55, 0.55, 0.22)
    scene.add(dice)

    let frameId = 0
    let completionTimer = 0
    let completed = false
    let landed = false

    const startedAt = performance.now()
    const spinSeed = rollKey % 17
    const reduceMotion = prefersReducedMotion()
    const duration = reduceMotion ? 280 : ROLL_DURATION_MS
    const resultHold = reduceMotion ? 360 : RESULT_HOLD_MS
    const revealAt = reduceMotion ? 0.18 : 0.58
    const profile = animationProfileForDie(die)
    const diceBounds = new Box3()
    const finalX = 0
    const finalY = 0
    const finalZ = 0
    const resultFaceMaterial = resultFace.material as MeshBasicMaterial
    const resultHaloMaterial = resultHalo.material as MeshBasicMaterial
    const sideLabelMaterials = sideLabelPlanes.map((labelPlane) => labelPlane.material as MeshBasicMaterial)

    const animate = (time: number) => {
      const rawProgress = Math.min((time - startedAt) / duration, 1)
      const tumblePhase = clamp01(rawProgress / 0.5)
      const settlePhase = clamp01((rawProgress - 0.5) / 0.5)
      const tumbleEase = easeOutCubic(tumblePhase)
      const settleEase = easeInOutCubic(settlePhase)
      const spinEnergy = reduceMotion ? 0 : 1 - settleEase
      const revealProgress = clamp01((rawProgress - revealAt) / (1 - revealAt || 1))
      const revealEase = easeOutCubic(revealProgress)
      const contactImpulse = reduceMotion
        ? 0
        : Math.max(0, Math.sin(rawProgress * Math.PI * (4.6 + profile.settle))) * (1 - tumbleEase * 0.5) * (1 - settleEase)
      const wobble = reduceMotion
        ? 0
        : Math.sin(rawProgress * Math.PI * (5.4 + profile.spin)) * (1 - settleEase) * 0.055 * profile.settle
      const resultPulse = revealEase > 0 && revealEase < 1
        ? Math.sin(revealEase * Math.PI) * 0.11
        : 0

      dice.rotation.x = finalX + spinEnergy * (Math.PI * 2.2 * profile.spin + spinSeed * 0.045) + wobble
      dice.rotation.y = finalY + spinEnergy * (Math.PI * 2.7 * profile.spin + result * 0.01) - wobble * 0.62
      dice.rotation.z = finalZ + spinEnergy * Math.PI * 1.65 * profile.spin + wobble * 0.38
      dice.position.x = reduceMotion
        ? 0
        : Math.sin(rawProgress * Math.PI * 4.2 + spinSeed * 0.16) * 0.022 * profile.travel * (1 - settleEase)
      dice.position.z = reduceMotion
        ? 0
        : Math.cos(rawProgress * Math.PI * 3.4 + result * 0.08) * 0.016 * profile.travel * (1 - settleEase)
      dice.scale.set(
        0.9 + contactImpulse * 0.026 + resultPulse,
        0.9 - contactImpulse * 0.016 + resultPulse,
        0.9 + contactImpulse * 0.026 + resultPulse,
      )
      dice.position.y = 0
      placeDiceOnTable(dice, bodyMesh, diceBounds, 0, TABLE_VISUAL_SINK)
      camera.position.x = reduceMotion ? 0 : Math.sin(rawProgress * Math.PI * 1.1) * 0.012 * (1 - settleEase * 0.6)
      camera.position.y = 0.35 + (reduceMotion ? 0 : Math.sin(rawProgress * Math.PI) * 0.008)
      camera.lookAt(0, -0.05, 0)
      contactShadow.position.x = dice.position.x
      contactShadow.position.z = dice.position.z + 0.04
      contactShadowMaterial.opacity = 0.42 + contactImpulse * 0.06
      contactShadow.scale.set(0.92 + contactImpulse * 0.08, 0.82 + contactImpulse * 0.04, 1)
      sideLabelMaterials.forEach((material) => {
        material.opacity = 1 - Math.max(settleEase, revealEase) * 0.82
      })
      if (rawProgress >= revealAt) {
        resultFace.visible = true
        resultHalo.visible = true
        resultFaceMaterial.opacity = Math.max(0.22, revealEase)
        resultHaloMaterial.opacity = Math.sin(revealEase * Math.PI) * 0.42
        resultHalo.scale.setScalar(1 + resultPulse * 1.6)
        if (!landed) {
          landed = true
          landedRef.current()
        }
      }
      render()

      if (rawProgress < 1) {
        frameId = window.requestAnimationFrame(animate)
        return
      }
      if (!completed) {
        completed = true
        resultFace.visible = true
        resultHalo.visible = true
        resultFaceMaterial.opacity = 1
        resultHaloMaterial.opacity = 0
        render()
        if (!landed) {
          landed = true
          landedRef.current()
        }
        completionTimer = window.setTimeout(() => completeRef.current(), resultHold)
      }
    }

    frameId = window.requestAnimationFrame(animate)

    return () => {
      window.cancelAnimationFrame(frameId)
      window.clearTimeout(completionTimer)
      dispose()
    }
  }, [die, result, rollKey])

  return <div ref={mountRef} className="dice-canvas" data-dice-engine="scripted" data-testid="dice-roller-canvas" />
}

function PhysicsDiceCanvas({
  die,
  result,
  rollKey,
  onLanded,
  onComplete,
}: DiceCanvasProps) {
  const mountRef = useRef<HTMLDivElement | null>(null)
  const completeRef = useRef(onComplete)
  const landedRef = useRef(onLanded)

  useEffect(() => {
    completeRef.current = onComplete
  }, [onComplete])

  useEffect(() => {
    landedRef.current = onLanded
  }, [onLanded])

  useEffect(() => {
    const mount = mountRef.current
    if (!mount) return undefined

    const { camera, contactShadow, contactShadowMaterial, render, scene, dispose } = createDiceStage(mount)
    const { group: dice, bodyMesh, resultFace, resultHalo, resultNormal, sideLabelPlanes } = createDiceGroup(die, result)
    const { shape, halfHeight } = createDiceCollider(bodyMesh.geometry)
    const profile = animationProfileForDie(die)
    const seedBase = rollKey * 97 + result * 13 + getDieSides(die)
    const resultQuaternion = createResultRestQuaternion(resultNormal, seedBase)
    const finalPosition = new Vector3(
      seededRange(seedBase + 15, -0.1, 0.1),
      0,
      seededRange(seedBase + 16, -0.06, 0.08),
    )
    const diceBounds = new Box3()

    dice.scale.setScalar(DICE_VISUAL_SCALE)
    scene.add(dice)

    const world = new World({
      gravity: new Vec3(0, -24, 0),
      allowSleep: true,
    })
    const solver = world.solver as World['solver'] & { iterations?: number; tolerance?: number }
    solver.iterations = 9
    solver.tolerance = 0.001

    const tableMaterial = new CannonMaterial('dice-table')
    const diceMaterial = new CannonMaterial('dice-body')
    world.defaultContactMaterial.friction = 0.78
    world.defaultContactMaterial.restitution = 0.04
    world.addContactMaterial(
      new ContactMaterial(diceMaterial, tableMaterial, {
        friction: 0.92,
        restitution: 0.08,
        contactEquationStiffness: 1e7,
        contactEquationRelaxation: 5,
        frictionEquationStiffness: 1e7,
        frictionEquationRelaxation: 6,
      }),
    )

    const tableQuaternion = new CannonQuaternion()
    tableQuaternion.setFromEuler(-Math.PI / 2, 0, 0)
    const tableBody = new Body({
      mass: 0,
      material: tableMaterial,
      position: new Vec3(0, TABLE_SURFACE_Y, 0),
      shape: new Plane(),
      quaternion: tableQuaternion,
    })
    world.addBody(tableBody)

    const startQuaternion = new CannonQuaternion()
    startQuaternion.setFromEuler(
      seededRange(seedBase + 3, -0.62, -0.24),
      seededRange(seedBase + 4, 0.58, 1.2),
      seededRange(seedBase + 5, -0.28, 0.42),
    )
    const startX = seededRange(seedBase + 1, -1.32, -1.12)
    const startZ = seededRange(seedBase + 2, -0.18, 0.18)
    const diceBody = new Body({
      mass: 1.35,
      material: diceMaterial,
      position: new Vec3(
        startX,
        TABLE_SURFACE_Y + halfHeight + 0.035,
        startZ,
      ),
      quaternion: startQuaternion,
      velocity: new Vec3(
        3.05 + profile.travel * 0.72 + seededRange(seedBase + 6, -0.16, 0.18),
        -0.12,
        seededRange(seedBase + 7, -0.38, 0.22),
      ),
      angularVelocity: new Vec3(
        4.2 + profile.spin * 2.1 + seededRange(seedBase + 8, -0.55, 0.55),
        5.8 + profile.spin * 2.6 + seededRange(seedBase + 9, -0.58, 0.58),
        -(7.6 + profile.spin * 3.2 + seededRange(seedBase + 10, -0.65, 0.65)),
      ),
      linearDamping: 0.12,
      angularDamping: 0.22,
      allowSleep: true,
      sleepSpeedLimit: 0.12,
      sleepTimeLimit: 0.16,
      shape,
    })
    world.addBody(diceBody)

    let frameId = 0
    let completionTimer = 0
    let completed = false
    let landed = false
    let revealStartedAt = 0
    let settledFor = 0
    let impactPulse = 0
    let wasNearTable = true
    let lastVerticalVelocity = diceBody.velocity.y
    let previousTime = performance.now()

    const startedAt = previousTime
    const resultFaceMaterial = resultFace.material as MeshBasicMaterial
    const resultHaloMaterial = resultHalo.material as MeshBasicMaterial
    const sideLabelMaterials = sideLabelPlanes.map((labelPlane) => labelPlane.material as MeshBasicMaterial)
    const revealAt = 0.82

    const animate = (time: number) => {
      const deltaSeconds = Math.min(Math.max((time - previousTime) / 1000, 0), 0.05)
      previousTime = time
      world.step(PHYSICS_STEP_SECONDS, deltaSeconds, PHYSICS_MAX_SUBSTEPS)

      const rawProgress = Math.min((time - startedAt) / PHYSICS_ROLL_DURATION_MS, 1)
      const settleBlend = easeInOutCubic(clamp01((rawProgress - 0.72) / 0.28))
      const bodyLift = Math.max(0, diceBody.position.y - (TABLE_SURFACE_Y + halfHeight))
      const physicsPosition = new Vector3(
        clamp(diceBody.position.x, -1.3, 1.22),
        0,
        clamp(diceBody.position.z, -0.42, 0.42),
      )
      const linearSpeed = diceBody.velocity.length()
      const angularSpeed = diceBody.angularVelocity.length()
      const nearTable = bodyLift < 0.065

      if (nearTable && !wasNearTable) {
        impactPulse = Math.min(0.9, impactPulse + Math.abs(lastVerticalVelocity) * 1.3 + angularSpeed * 0.025)
      }
      wasNearTable = nearTable
      lastVerticalVelocity = diceBody.velocity.y
      impactPulse = Math.max(0, impactPulse - deltaSeconds * 5.4)

      if (rawProgress > 0.5) {
        const brake = settleBlend * settleBlend
        diceBody.velocity.scale(1 - brake * 0.42, diceBody.velocity)
        diceBody.angularVelocity.scale(1 - brake * 0.58, diceBody.angularVelocity)
      }

      if (rawProgress > 0.72 && linearSpeed < 0.2 && angularSpeed < 0.36) {
        settledFor += deltaSeconds
      } else {
        settledFor = 0
      }

      dice.position.copy(physicsPosition).lerp(finalPosition, settleBlend)
      dice.quaternion.set(diceBody.quaternion.x, diceBody.quaternion.y, diceBody.quaternion.z, diceBody.quaternion.w)
      dice.quaternion.slerp(resultQuaternion, settleBlend)
      const hopEnergy = Math.min(bodyLift * 0.45, MAX_PHYSICS_VISUAL_HOP * (1 - settleBlend))
      const rattle = Math.max(
        0,
        Math.sin(rawProgress * Math.PI * (17 + profile.settle * 2)) * (1 - settleBlend) * 0.004,
      )
      placeDiceOnTable(dice, bodyMesh, diceBounds, hopEnergy + rattle + impactPulse * 0.01, 0.035)

      if (settleBlend > 0.66) {
        diceBody.position.set(dice.position.x, dice.position.y, dice.position.z)
        diceBody.quaternion.set(dice.quaternion.x, dice.quaternion.y, dice.quaternion.z, dice.quaternion.w)
        diceBody.velocity.scale(0.3, diceBody.velocity)
        diceBody.angularVelocity.scale(0.22, diceBody.angularVelocity)
      }

      const shadowStrength = clamp01(1 - hopEnergy * 4.5)
      const speedEnergy = clamp01((linearSpeed + angularSpeed * 0.08) / 3.6)
      contactShadow.position.x = dice.position.x
      contactShadow.position.z = dice.position.z + 0.04
      contactShadowMaterial.opacity = 0.2 + shadowStrength * 0.3 + impactPulse * 0.08
      contactShadow.scale.set(
        0.72 + shadowStrength * 0.16 + speedEnergy * 0.1,
        0.62 + shadowStrength * 0.12 + impactPulse * 0.08,
        1,
      )

      camera.position.x = Math.sin(rawProgress * Math.PI * 1.08) * 0.014 * (1 - settleBlend)
      camera.position.y = 0.35 + Math.sin(rawProgress * Math.PI) * 0.01 * (1 - settleBlend * 0.6)
      camera.lookAt(0, -0.05, 0)

      if ((rawProgress >= revealAt || settledFor >= 0.16) && !landed) {
        landed = true
        revealStartedAt = time
        landedRef.current()
      }

      const revealEase = landed ? easeOutCubic(clamp01((time - revealStartedAt) / 260)) : 0
      const resultPulse = revealEase > 0 && revealEase < 1
        ? Math.sin(revealEase * Math.PI) * 0.1
        : 0
      sideLabelMaterials.forEach((material) => {
        material.opacity = 1 - Math.max(settleBlend, revealEase) * 0.86
      })
      if (landed) {
        resultFace.visible = true
        resultHalo.visible = true
        resultFaceMaterial.opacity = Math.max(0.22, revealEase)
        resultHaloMaterial.opacity = Math.sin(revealEase * Math.PI) * 0.42
        resultHalo.scale.setScalar(1 + resultPulse * 1.5)
        dice.scale.setScalar(DICE_VISUAL_SCALE + resultPulse)
      } else {
        dice.scale.setScalar(DICE_VISUAL_SCALE)
      }

      render()

      if (rawProgress < 1) {
        frameId = window.requestAnimationFrame(animate)
        return
      }

      if (!completed) {
        completed = true
        resultFace.visible = true
        resultHalo.visible = true
        resultFaceMaterial.opacity = 1
        resultHaloMaterial.opacity = 0
        dice.scale.setScalar(DICE_VISUAL_SCALE)
        render()
        if (!landed) {
          landed = true
          landedRef.current()
        }
        completionTimer = window.setTimeout(() => completeRef.current(), RESULT_HOLD_MS)
      }
    }

    frameId = window.requestAnimationFrame(animate)

    return () => {
      window.cancelAnimationFrame(frameId)
      window.clearTimeout(completionTimer)
      world.removeBody(diceBody)
      world.removeBody(tableBody)
      dispose()
    }
  }, [die, result, rollKey])

  return <div ref={mountRef} className="dice-canvas" data-dice-engine="physics" data-testid="dice-roller-canvas" />
}

function DiceCanvas(props: DiceCanvasProps) {
  const engine = getConfiguredDiceAnimationEngine()
  if (engine === 'physics' && !prefersReducedMotion()) {
    return <PhysicsDiceCanvas {...props} />
  }
  return <ScriptedDiceCanvas {...props} />
}

export default function DiceRollDialog({
  die,
  result,
  modifier,
  total,
  targetLabel,
  rollKey,
  status,
  onCancel,
  onComplete,
}: DiceRollDialogProps) {
  const [landedRollKey, setLandedRollKey] = useState<number | null>(null)
  const hasLanded = landedRollKey === rollKey
  const isSending = status === 'sending'
  const modifierLabel = signedModifier(modifier)
  const shownResult = hasLanded || isSending
  const primaryResult = shownResult ? (modifier ? total : result) : '...'
  const title = isSending ? 'Sending roll' : hasLanded ? 'Landed' : 'Rolling dice'
  const statusText = isSending
    ? 'Sending to chat...'
    : hasLanded
      ? 'Landed. Sending roll...'
      : 'Still tumbling...'
  const readoutLabel = shownResult
    ? `${die.toUpperCase()} roll ${primaryResult}${modifierLabel ? `, die ${result} ${modifierLabel} equals ${total}` : ''}. ${statusText}`
    : `${die.toUpperCase()} rolling. ${statusText}`

  return (
    <section
      className={`dice-dialog ${status}`}
      role="dialog"
      aria-modal="true"
      aria-labelledby="dice-roll-title"
    >
      <header>
        <div>
          <span>{die.toUpperCase()} roll</span>
          <h2 id="dice-roll-title">{title}</h2>
        </div>
        <button type="button" aria-label="Close dice roller" onClick={onCancel} disabled={isSending}>
          <X size={18} />
        </button>
      </header>
      <div className="dice-stage">
        <DiceCanvas
          die={die}
          result={result}
          rollKey={rollKey}
          onLanded={() => setLandedRollKey(rollKey)}
          onComplete={onComplete}
        />
        <div className="dice-readout" aria-label={readoutLabel} aria-live="polite">
          <span>{die.toUpperCase()}</span>
          <strong>{primaryResult}</strong>
          {shownResult && modifierLabel ? (
            <small>
              Die {result} {modifierLabel} = {total}
            </small>
          ) : null}
          <small>{statusText}</small>
          {targetLabel ? <small>Target: {targetLabel}</small> : null}
        </div>
      </div>
    </section>
  )
}
