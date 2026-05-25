import logoImg from '@/assets/logo.png'

const blobs = [
  { w: '90%', h: '90%', top: '0%',  left: '5%',  anim: 'orb-drift-1 4s ease-in-out infinite' },
  { w: '80%', h: '80%', top: '10%', left: '15%', anim: 'orb-drift-2 5s ease-in-out infinite' },
  { w: '75%', h: '75%', top: '15%', left: '0%',  anim: 'orb-drift-3 4.5s ease-in-out infinite' },
  { w: '70%', h: '70%', top: '5%',  left: '20%', anim: 'orb-drift-4 5.5s ease-in-out infinite' },
]

/**
 * Logo with a Siri-like orb glow behind it.
 * The logo stays at its original size with no border/frame;
 * the animated blobs sit behind it as a vivid halo.
 */
export default function OrbLogo({ size = 80 }: { size?: number }) {
  const glowSize = Math.round(size * 1.9)
  const glowOffset = Math.round((glowSize - size) / 2)

  return (
    <div
      className="relative flex items-center justify-center"
      style={{ width: size, height: size }}
    >
      {/* glow layer behind logo */}
      <div
        className="absolute"
        style={{
          width: glowSize,
          height: glowSize,
          top: -glowOffset,
          left: -glowOffset,
          borderRadius: '50%',
          filter: `blur(${Math.round(size * 0.22)}px)`,
          opacity: 0.85,
          animation: 'orb-rotate 6s ease-in-out infinite',
        }}
      >
        {blobs.map((b, i) => (
          <div
            key={i}
            className="absolute"
            style={{
              width: b.w, height: b.h, top: b.top, left: b.left,
              borderRadius: '50%',
              mixBlendMode: 'screen',
              background: `radial-gradient(circle, var(--orb-color-${i + 1}), transparent 70%)`,
              animation: b.anim,
            }}
          />
        ))}
      </div>

      {/* logo — original size, no border, no frame */}
      <img
        src={logoImg}
        alt="OpenGIS"
        className="relative z-[2] rounded-2xl"
        style={{ width: size, height: size, objectFit: 'contain' }}
      />
    </div>
  )
}
