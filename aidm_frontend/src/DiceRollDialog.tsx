import { useEffect, useRef, useState, type RefObject } from 'react'
import { X } from 'lucide-react'
import type { RollResolvedPayload } from './gameActions'
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

type DiceRollStatus = 'requesting' | 'rolling' | 'resolved' | 'failed'

type DiceRollDialogProps = {
  die: string
  result: number | null
  rolls: number[] | null
  mode: 'normal' | 'advantage' | 'disadvantage'
  modifier: number | null
  total: number | null
  provenance?: Pick<RollResolvedPayload, 'ability' | 'proficiency' | 'modifier_breakdown'> | null
  targetLabel?: string | null
  rollKey: number
  status: DiceRollStatus
  dialogRef?: RefObject<HTMLElement | null>
  error?: string
  onCancel: () => void
  onComplete: () => void
  onRetry: () => void
}

type DiceAnimationEngine = 'scripted' | 'physics'

type DiceCanvasProps = {
  die: string
  result: number
  rollKey: number
  onLanded: () => void
  onComplete: () => void
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

const SCRIPTED_ROLL_DURATION_MS = 900
const PHYSICS_ROLL_DURATION_MS = 1850
const RESULT_HOLD_MS = 850
const FACE_LABEL_DEPTH_OFFSET = 0.045
const FRONT_NORMAL = new Vector3(0, 0, 1)
const CAMERA_RESULT_NORMAL = new Vector3(0, 0.42, 0.91).normalize()
const TABLE_SURFACE_Y = -1.08
const TABLE_VISUAL_SINK = 0.024
const TABLE_GROUND_PADDING = 0.012
const DICE_VISUAL_SCALE = 0.68
const PHYSICS_STEP_SECONDS = 1 / 90
const PHYSICS_MAX_SUBSTEPS = 5
const DICE_ANIMATION_STORAGE_KEY = 'aidm:diceAnimationEngine'
const DEFAULT_DICE_ANIMATION_ENGINE: DiceAnimationEngine = 'physics'

const DIE_SIDES: Record<string, number> = {
  d4: 4,
  d6: 6,
  d8: 8,
  d10: 10,
  d12: 12,
  d20: 20,
  d100: 100,
}

function easeOutCubic(value: number) {
  return 1 - (1 - value) ** 3
}

function easeInOutCubic(value: number) {
  return value < 0.5 ? 4 * value ** 3 : 1 - (-2 * value + 2) ** 3 / 2
}

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max)
}

function clamp01(value: number) {
  return clamp(value, 0, 1)
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
      // Storage is not always available in private/test environments.
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

function signedModifier(value: number) {
  if (!value) return ''
  return value > 0 ? `+${value}` : String(value)
}

function animationProfileForDie(die: string): DiceAnimationProfile {
  const normalizedDie = die.toLowerCase()
  if (normalizedDie === 'd4') return { bounce: 0.68, settle: 1.16, spin: 0.72, travel: 0.62 }
  if (normalizedDie === 'd6') return { bounce: 0.78, settle: 1.08, spin: 0.8, travel: 0.68 }
  if (normalizedDie === 'd8') return { bounce: 0.84, settle: 1.02, spin: 0.88, travel: 0.74 }
  if (normalizedDie === 'd10') return { bounce: 0.82, settle: 1, spin: 0.9, travel: 0.78 }
  if (normalizedDie === 'd12') return { bounce: 0.86, settle: 0.96, spin: 0.94, travel: 0.82 }
  if (normalizedDie === 'd100') return { bounce: 0.8, settle: 0.98, spin: 0.86, travel: 0.74 }
  return { bounce: 0.9, settle: 0.92, spin: 1, travel: 0.9 }
}

function createResultRestQuaternion(resultNormal: Vector3, seed: number) {
  const readableNormal = CAMERA_RESULT_NORMAL.clone()
  readableNormal.x += seededRange(seed + 31, -0.055, 0.055)
  readableNormal.y += seededRange(seed + 32, -0.035, 0.045)
  readableNormal.normalize()

  const base = new Quaternion().setFromUnitVectors(resultNormal.clone().normalize(), readableNormal)
  const twist = new Quaternion().setFromAxisAngle(readableNormal, seededRange(seed + 33, -0.2, 0.2))
  return twist.multiply(base).normalize()
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

    context.fillStyle = isResult ? 'rgba(8, 16, 16, 0.9)' : 'rgba(8, 16, 16, 0.68)'
    context.strokeStyle = isResult ? 'rgba(255, 218, 166, 0.98)' : 'rgba(255, 218, 166, 0.72)'
    context.lineWidth = isResult ? 9 : 6
    context.beginPath()
    context.arc(center, center, badgeRadius, 0, Math.PI * 2)
    context.fill()
    context.stroke()
    context.shadowColor = 'rgba(0, 0, 0, 0.6)'
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
    gradient.addColorStop(0, 'rgba(0, 0, 0, 0.74)')
    gradient.addColorStop(0.44, 'rgba(0, 0, 0, 0.36)')
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
  const resultFaceSample = faceSamples.reduce((best, sample) => (sample.normal.z > best.normal.z ? sample : best), faceSamples[0])
  const sideFaces = faceSamples.filter((sample) => sample !== resultFaceSample && sample.normal.dot(resultFaceSample.normal) < 0.96)
  const sideLabels = pickEvenly(sideFaces, Math.min(getDieSides(normalizedDie) - 1, 22, sideFaces.length))
  const sideValues = sideLabelValues(normalizedDie, result, sideLabels.length)
  const group = new Group()
  const bodyMesh = new Mesh(
    geometry,
    new MeshStandardMaterial({
      color: 0xc64f22,
      emissive: 0x2f0c04,
      metalness: 0.2,
      roughness: 0.46,
      flatShading: true,
    }),
  )
  const edges = new LineSegments(
    new EdgesGeometry(geometry),
    new LineBasicMaterial({
      color: 0xffd19a,
      transparent: true,
      opacity: 0.7,
    }),
  )
  const resultFace = createNumberPlane(String(result), resultFaceSample, true)
  const resultHalo = createResultHalo(String(result), resultFaceSample)
  const sideLabelPlanes: Mesh[] = []

  bodyMesh.castShadow = true
  bodyMesh.receiveShadow = true
  group.add(bodyMesh, edges)
  sideLabels.forEach((sample, index) => {
    const labelPlane = createNumberPlane(String(sideValues[index]), sample)
    sideLabelPlanes.push(labelPlane)
    group.add(labelPlane)
  })
  group.add(resultHalo, resultFace)
  resultFace.visible = false

  return {
    group,
    bodyMesh,
    resultFace,
    resultHalo,
    resultNormal: resultFaceSample.normal.clone(),
    sideLabelPlanes,
  }
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
  return { shape, halfHeight: Math.max(Math.abs(minY), Math.abs(maxY), 0.7) }
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

function createDiceStage(mount: HTMLDivElement) {
  const renderer = new WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
  renderer.shadowMap.enabled = true
  renderer.outputColorSpace = SRGBColorSpace
  renderer.domElement.setAttribute('aria-hidden', 'true')
  mount.appendChild(renderer.domElement)

  const scene = new Scene()
  const camera = new PerspectiveCamera(38, 1, 0.1, 100)
  camera.position.set(0, 0.52, 5.45)

  const keyLight = new DirectionalLight(0xffdfbd, 2.7)
  keyLight.position.set(3.2, 4.4, 4.2)
  keyLight.castShadow = true
  keyLight.shadow.mapSize.width = 1024
  keyLight.shadow.mapSize.height = 1024
  keyLight.shadow.camera.left = -3.4
  keyLight.shadow.camera.right = 3.4
  keyLight.shadow.camera.top = 2.4
  keyLight.shadow.camera.bottom = -2.4
  keyLight.shadow.camera.near = 0.2
  keyLight.shadow.camera.far = 12

  const rimLight = new DirectionalLight(0x72b59b, 1.18)
  rimLight.position.set(-4, 1.2, -2.4)
  scene.add(new AmbientLight(0xffffff, 0.56), keyLight, rimLight)

  const table = new Mesh(
    new PlaneGeometry(6.6, 3.35),
    new MeshStandardMaterial({
      color: 0x182526,
      roughness: 0.84,
      metalness: 0.02,
    }),
  )
  table.position.set(0, TABLE_SURFACE_Y, -0.24)
  table.rotation.x = -Math.PI / 2
  table.receiveShadow = true
  scene.add(table)

  const contactShadowMaterial = new MeshBasicMaterial({
    map: createContactShadowTexture(),
    transparent: true,
    opacity: 0.34,
    side: DoubleSide,
    depthWrite: false,
  })
  const contactShadow = new Mesh(new PlaneGeometry(1.74, 0.94), contactShadowMaterial)
  contactShadow.position.set(0, TABLE_SURFACE_Y + 0.026, -0.16)
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
    const resultQuaternion = createResultRestQuaternion(new Vector3(0, 0, 1), rollKey + result)
    const diceBounds = new Box3()
    const reduceMotion = prefersReducedMotion()
    const duration = reduceMotion ? 80 : SCRIPTED_ROLL_DURATION_MS
    const resultHold = reduceMotion ? 160 : RESULT_HOLD_MS
    const profile = animationProfileForDie(die)
    const resultFaceMaterial = resultFace.material as MeshBasicMaterial
    const resultHaloMaterial = resultHalo.material as MeshBasicMaterial
    const sideLabelMaterials = sideLabelPlanes.map((labelPlane) => labelPlane.material as MeshBasicMaterial)
    const startedAt = performance.now()
    const spinSeed = rollKey % 17
    let frameId = 0
    let completionTimer = 0
    let landed = false
    let completed = false

    dice.scale.setScalar(DICE_VISUAL_SCALE)
    scene.add(dice)

    const animate = (time: number) => {
      const rawProgress = Math.min((time - startedAt) / duration, 1)
      const settleBlend = easeInOutCubic(clamp01((rawProgress - 0.56) / 0.44))
      const revealEase = easeOutCubic(clamp01((rawProgress - 0.68) / 0.22))
      const energy = reduceMotion ? 0 : 1 - settleBlend
      const wobble = Math.sin(rawProgress * Math.PI * 7.8) * energy * 0.09 * profile.settle
      const impact = Math.max(0, Math.sin(rawProgress * Math.PI * 5.8)) * energy * 0.11 * profile.bounce
      const resultPulse = revealEase > 0 && revealEase < 1 ? Math.sin(revealEase * Math.PI) * 0.1 : 0

      dice.position.set(
        reduceMotion ? 0 : Math.sin(rawProgress * Math.PI * 2.1 + spinSeed) * 0.26 * energy,
        TABLE_SURFACE_Y + 0.74 + impact,
        reduceMotion ? 0 : Math.cos(rawProgress * Math.PI * 1.6 + result) * 0.08 * energy,
      )
      dice.rotation.set(
        -0.46 + energy * (Math.PI * 2.6 + spinSeed * 0.06) + wobble,
        0.28 + energy * (Math.PI * 3.4 + result * 0.018) - wobble * 0.4,
        0.04 + energy * Math.PI * 2.1 + wobble * 0.35,
      )
      dice.quaternion.slerp(resultQuaternion, settleBlend)
      dice.scale.setScalar(DICE_VISUAL_SCALE + resultPulse)
      if (settleBlend > 0.04) {
        placeDiceOnTable(dice, bodyMesh, diceBounds, impact * (1 - settleBlend), TABLE_VISUAL_SINK)
      }

      contactShadow.position.x = dice.position.x
      contactShadow.position.z = dice.position.z + 0.04
      contactShadowMaterial.opacity = 0.26 + settleBlend * 0.18 + impact * 0.12
      contactShadow.scale.set(0.78 + impact * 0.12, 0.68 + impact * 0.08, 1)
      camera.position.x = reduceMotion ? 0 : Math.sin(rawProgress * Math.PI * 1.08) * 0.016 * energy
      camera.position.y = 0.52 + (reduceMotion ? 0 : Math.sin(rawProgress * Math.PI) * 0.018 * energy)
      camera.lookAt(0, -0.12, 0)

      sideLabelMaterials.forEach((material) => {
        material.opacity = 1 - Math.max(settleBlend, revealEase) * 0.86
      })

      if (rawProgress >= 0.68) {
        resultFace.visible = true
        resultHalo.visible = true
        resultFaceMaterial.opacity = Math.max(0.22, revealEase)
        resultHaloMaterial.opacity = Math.sin(revealEase * Math.PI) * 0.44
        resultHalo.scale.setScalar(1 + resultPulse * 1.5)
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
    const targetPosition = new Vector3(
      seededRange(seedBase + 15, -0.14, 0.14),
      TABLE_SURFACE_Y + halfHeight + TABLE_GROUND_PADDING,
      seededRange(seedBase + 16, -0.08, 0.1),
    )
    const diceBounds = new Box3()

    dice.scale.setScalar(DICE_VISUAL_SCALE)
    scene.add(dice)

    const world = new World({
      gravity: new Vec3(0, -18.5, 0),
      allowSleep: true,
    })
    const solver = world.solver as World['solver'] & { iterations?: number; tolerance?: number }
    solver.iterations = 10
    solver.tolerance = 0.001

    const tableMaterial = new CannonMaterial('dice-table')
    const diceMaterial = new CannonMaterial('dice-body')
    world.defaultContactMaterial.friction = 0.38
    world.defaultContactMaterial.restitution = 0.22
    world.addContactMaterial(
      new ContactMaterial(diceMaterial, tableMaterial, {
        friction: 0.48,
        restitution: 0.28,
        contactEquationStiffness: 1e7,
        contactEquationRelaxation: 4,
        frictionEquationStiffness: 1e7,
        frictionEquationRelaxation: 5,
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
      seededRange(seedBase + 3, -1.0, -0.18),
      seededRange(seedBase + 4, 0.62, 1.55),
      seededRange(seedBase + 5, -0.55, 0.62),
    )
    const startX = seededRange(seedBase + 1, -1.78, -1.46)
    const startZ = seededRange(seedBase + 2, -0.24, 0.22)
    const diceBody = new Body({
      mass: 1.16,
      material: diceMaterial,
      position: new Vec3(
        startX,
        TABLE_SURFACE_Y + halfHeight + 0.72,
        startZ,
      ),
      quaternion: startQuaternion,
      velocity: new Vec3(
        2.45 + profile.travel * 0.9 + seededRange(seedBase + 6, -0.24, 0.24),
        -1.45 + seededRange(seedBase + 11, -0.18, 0.16),
        seededRange(seedBase + 7, -0.46, 0.34),
      ),
      angularVelocity: new Vec3(
        8.8 + profile.spin * 3.4 + seededRange(seedBase + 8, -1.1, 1.1),
        10.6 + profile.spin * 3.8 + seededRange(seedBase + 9, -1.2, 1.2),
        -(12.4 + profile.spin * 4.2 + seededRange(seedBase + 10, -1.3, 1.3)),
      ),
      linearDamping: 0.035,
      angularDamping: 0.075,
      allowSleep: true,
      sleepSpeedLimit: 0.055,
      sleepTimeLimit: 0.34,
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
    let wasNearTable = false
    let lastVerticalVelocity = diceBody.velocity.y
    let previousTime = performance.now()

    const startedAt = previousTime
    const resultFaceMaterial = resultFace.material as MeshBasicMaterial
    const resultHaloMaterial = resultHalo.material as MeshBasicMaterial
    const sideLabelMaterials = sideLabelPlanes.map((labelPlane) => labelPlane.material as MeshBasicMaterial)
    const guideStart = 0.82

    const animate = (time: number) => {
      const deltaSeconds = Math.min(Math.max((time - previousTime) / 1000, 0), 0.05)
      previousTime = time
      world.step(PHYSICS_STEP_SECONDS, deltaSeconds, PHYSICS_MAX_SUBSTEPS)

      const rawProgress = Math.min((time - startedAt) / PHYSICS_ROLL_DURATION_MS, 1)
      const guideBlend = easeInOutCubic(clamp01((rawProgress - guideStart) / (1 - guideStart)))
      const linearSpeed = diceBody.velocity.length()
      const angularSpeed = diceBody.angularVelocity.length()
      const bodyLift = Math.max(0, diceBody.position.y - (TABLE_SURFACE_Y + halfHeight))
      const nearTable = bodyLift < 0.12

      if (nearTable && !wasNearTable) {
        impactPulse = Math.min(1, impactPulse + Math.abs(lastVerticalVelocity) * 0.5 + angularSpeed * 0.018)
      }
      wasNearTable = nearTable
      lastVerticalVelocity = diceBody.velocity.y
      impactPulse = Math.max(0, impactPulse - deltaSeconds * 4.6)

      const softBrake = easeInOutCubic(clamp01((rawProgress - 0.64) / 0.28))
      diceBody.velocity.scale(1 - softBrake * 0.035, diceBody.velocity)
      diceBody.angularVelocity.scale(1 - softBrake * 0.055, diceBody.angularVelocity)
      if (guideBlend > 0.82) {
        diceBody.velocity.scale(0.78, diceBody.velocity)
        diceBody.angularVelocity.scale(0.68, diceBody.angularVelocity)
      }

      if (rawProgress > guideStart && linearSpeed < 0.2 && angularSpeed < 0.36) {
        settledFor += deltaSeconds
      } else {
        settledFor = 0
      }

      const physicsPosition = new Vector3(
        clamp(diceBody.position.x, -1.58, 1.42),
        diceBody.position.y,
        clamp(diceBody.position.z, -0.5, 0.5),
      )
      dice.position.copy(physicsPosition).lerp(targetPosition, guideBlend)
      dice.quaternion.set(diceBody.quaternion.x, diceBody.quaternion.y, diceBody.quaternion.z, diceBody.quaternion.w)
      dice.quaternion.slerp(resultQuaternion, guideBlend)
      if (guideBlend > 0.94) {
        placeDiceOnTable(dice, bodyMesh, diceBounds, 0, 0.02)
      }

      const visualLift = Math.max(0, dice.position.y - targetPosition.y)
      const shadowStrength = clamp01(1 - visualLift * 0.92)
      const speedEnergy = clamp01((linearSpeed + angularSpeed * 0.08) / 5.2)
      contactShadow.position.x = dice.position.x
      contactShadow.position.z = dice.position.z + 0.04
      contactShadowMaterial.opacity = 0.18 + shadowStrength * 0.34 + impactPulse * 0.12
      contactShadow.scale.set(
        0.76 + shadowStrength * 0.18 + speedEnergy * 0.14,
        0.64 + shadowStrength * 0.16 + impactPulse * 0.12,
        1,
      )

      camera.position.x = Math.sin(rawProgress * Math.PI * 1.08) * 0.018 * (1 - guideBlend)
      camera.position.y = 0.52 + Math.sin(rawProgress * Math.PI) * 0.02 * (1 - guideBlend * 0.6)
      camera.lookAt(0, -0.12, 0)

      if ((rawProgress >= guideStart || settledFor >= 0.16) && !landed) {
        landed = true
        revealStartedAt = time
        landedRef.current()
      }

      const revealEase = landed ? easeOutCubic(clamp01((time - revealStartedAt) / 320)) : 0
      const resultPulse = revealEase > 0 && revealEase < 1 ? Math.sin(revealEase * Math.PI) * 0.12 : 0
      sideLabelMaterials.forEach((material) => {
        material.opacity = 1 - Math.max(guideBlend, revealEase) * 0.88
      })
      if (landed) {
        resultFace.visible = true
        resultHalo.visible = true
        resultFaceMaterial.opacity = Math.max(0.24, revealEase)
        resultHaloMaterial.opacity = Math.sin(revealEase * Math.PI) * 0.46
        resultHalo.scale.setScalar(1 + resultPulse * 1.55)
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
  rolls,
  mode,
  modifier,
  total,
  provenance,
  targetLabel,
  rollKey,
  status,
  dialogRef,
  error,
  onCancel,
  onComplete,
  onRetry,
}: DiceRollDialogProps) {
  const [landedRollKey, setLandedRollKey] = useState<number | null>(null)
  const hasLanded = landedRollKey === rollKey
  const authoritativeResult = result !== null && modifier !== null && total !== null
  const modifierLabel = signedModifier(modifier ?? 0)
  const shownResult = authoritativeResult && (hasLanded || status === 'resolved')
  const primaryResult = shownResult ? (modifier ? total : result) : '...'
  const title = status === 'requesting'
    ? 'Requesting roll'
    : status === 'failed'
      ? 'Roll not confirmed'
      : status === 'resolved' || hasLanded
        ? 'Landed'
        : 'Rolling dice'
  const statusText = status === 'requesting'
    ? 'Waiting for the authoritative server result...'
    : status === 'failed'
      ? error ?? 'The roll request was not confirmed.'
      : status === 'resolved'
        ? 'Authoritative result received.'
        : hasLanded
          ? 'Authoritative result landed.'
          : 'Authoritative result received. Still tumbling...'
  const readoutLabel = shownResult
    ? `${die.toUpperCase()} roll ${primaryResult}${modifierLabel ? `, die ${result} ${modifierLabel} equals ${total}` : ''}. ${statusText}`
    : `${die.toUpperCase()} rolling. ${statusText}`

  return (
    <section
      ref={dialogRef}
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
        <button type="button" aria-label="Close dice roller" onClick={onCancel} data-autofocus>
          <X size={18} />
        </button>
      </header>
      <div className="dice-stage">
        {status === 'rolling' && authoritativeResult ? (
          <DiceCanvas
            die={die}
            result={result}
            rollKey={rollKey}
            onLanded={() => setLandedRollKey(rollKey)}
            onComplete={onComplete}
          />
        ) : (
          <div className={`dice-request-state ${status}`} aria-hidden="true">
            <span>{status === 'failed' ? '!' : die.toUpperCase()}</span>
          </div>
        )}
        <div className="dice-readout" aria-label={readoutLabel} aria-live="polite">
          <span>{die.toUpperCase()}</span>
          <strong>{primaryResult}</strong>
          {shownResult && modifierLabel ? (
            <small>
              Die {result} {modifierLabel} = {total}
            </small>
          ) : null}
          {shownResult && rolls && rolls.length > 1 ? (
            <small>
              {mode === 'advantage' ? 'Advantage' : 'Disadvantage'} rolls {rolls.join(', ')}; kept {result}
            </small>
          ) : null}
          {shownResult && provenance ? (
            <div className="dice-private-provenance" aria-label="Private roll details">
              {provenance.ability ? (
                <small>
                  {provenance.ability.label} score {provenance.ability.score ?? '—'} ({signedModifier(provenance.ability.modifier) || '+0'})
                </small>
              ) : null}
              {provenance.proficiency && (provenance.proficiency.bonus || provenance.proficiency.skills.length) ? (
                <small>
                  Proficiency {signedModifier(provenance.proficiency.bonus) || '+0'}
                  {provenance.proficiency.skills.length ? `: ${provenance.proficiency.skills.join(', ')}` : ''}
                </small>
              ) : null}
              {provenance.modifier_breakdown ? (
                <small>
                  Modifier: ability {signedModifier(provenance.modifier_breakdown.ability_modifier) || '+0'}, proficiency{' '}
                  {signedModifier(provenance.modifier_breakdown.proficiency_bonus) || '+0'}, wounds{' '}
                  {provenance.modifier_breakdown.wound_penalty ? `-${provenance.modifier_breakdown.wound_penalty}` : '0'}
                </small>
              ) : null}
            </div>
          ) : null}
          <small>{statusText}</small>
          {targetLabel ? <small>Target: {targetLabel}</small> : null}
          {status === 'failed' ? (
            <button type="button" className="dice-retry-button" onClick={onRetry} data-autofocus>
              Retry safely
            </button>
          ) : null}
        </div>
      </div>
    </section>
  )
}
