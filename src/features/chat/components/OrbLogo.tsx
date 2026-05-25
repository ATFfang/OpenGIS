import logoImg from '@/assets/logo.png'

/**
 * Logo with subtle Siri-like animated glow behind it.
 * Soft blurred color blobs — no hard edges, no visible circles.
 */
export default function OrbLogo({ size = 80 }: { size?: number }) {
  const glowSize = Math.round(size * 1.7)
  const glowOffset = Math.round((glowSize - size) / 2)
  const blur = Math.round(size * 0.28)

  return (
    <div
      className="relative flex items-center justify-center"
      style={{ width: size, height: size }}
    >
      {/* Soft glow layer — heavily blurred so no visible circle edges */}
      <div
        className="absolute"
        style={{
          width: glowSize,
          height: glowSize,
          top: -glowOffset,
          left: -glowOffset,
          filter: `blur(${blur}px)`,
          opacity: 0.8,
          animation: 'orb-rotate 7s ease-in-out infinite',
        }}
      >
        {/* Blob 1 */}
        <div
          className="absolute"
          style={{
            width: '80%', height: '80%', top: '5%', left: '10%',
            borderRadius: '50%',
            background: 'radial-gradient(circle, var(--orb-color-1), transparent 60%)',
            animation: 'orb-drift-1 4s ease-in-out infinite',
          }}
        />
        {/* Blob 2 */}
        <div
          className="absolute"
          style={{
            width: '70%', height: '70%', top: '15%', left: '20%',
            borderRadius: '50%',
            background: 'radial-gradient(circle, var(--orb-color-2), transparent 60%)',
            animation: 'orb-drift-2 5s ease-in-out infinite',
          }}
        />
        {/* Blob 3 */}
        <div
          className="absolute"
          style={{
            width: '65%', height: '65%', top: '18%', left: '2%',
            borderRadius: '50%',
            background: 'radial-gradient(circle, var(--orb-color-3), transparent 60%)',
            animation: 'orb-drift-3 4.5s ease-in-out infinite',
          }}
        />
        {/* Blob 4 */}
        <div
          className="absolute"
          style={{
            width: '55%', height: '55%', top: '8%', left: '25%',
            borderRadius: '50%',
            background: 'radial-gradient(circle, var(--orb-color-4), transparent 60%)',
            animation: 'orb-drift-4 5.5s ease-in-out infinite',
          }}
        />
        {/* Blob 5 */}
        <div
          className="absolute"
          style={{
            width: '50%', height: '50%', top: '25%', left: '28%',
            borderRadius: '50%',
            background: 'radial-gradient(circle, var(--orb-color-5), transparent 55%)',
            animation: 'orb-drift-5 3.5s ease-in-out infinite',
          }}
        />
      </div>

      {/* Logo */}
      <img
        src={logoImg}
        alt="OpenGIS"
        className="relative z-[2] rounded-2xl"
        style={{ width: size, height: size, objectFit: 'contain' }}
      />
    </div>
  )
}
