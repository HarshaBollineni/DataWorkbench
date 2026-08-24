import { useState } from "react"

// Genpact brand palette (genpact-brand-guidelines): Sunset Orange #FFAD28 is the
// primary accent; Midnight Black #181C23 carries the wordmark on light surfaces.
const SUNSET_ORANGE = "#FFAD28"
const MIDNIGHT_BLACK = "#181C23"

/**
 * Genpact "on it" lockup, sourced from genpact.com (Scene7 CDN original).
 * /genpact-logo-white.svg = official white variant (dark surfaces);
 * /genpact-logo.svg = same artwork recolored Midnight Black (light surfaces).
 * Falls back to a text wordmark if the assets are removed, so the brand
 * never disappears from screen. Size follows font-size (text-* classes).
 */
export function BrandLogo({ className = "", dark = false }) {
  const [assetMissing, setAssetMissing] = useState(false)
  return (
    <span className={`inline-flex items-baseline leading-none ${className}`}>
      {!assetMissing ? (
        <img
          src={dark ? "/genpact-logo-white.svg" : "/genpact-logo.svg"}
          alt="Genpact"
          style={{ height: "2em" }}
          onError={() => setAssetMissing(true)}
        />
      ) : (
        <>
          <span
            className="font-extrabold tracking-tight"
            style={{ color: dark ? "#FFFFFF" : MIDNIGHT_BLACK }}
          >
            genpact
          </span>
          <span
            className="ml-0.5 inline-block rounded-full"
            style={{ width: "0.3em", height: "0.3em", backgroundColor: SUNSET_ORANGE }}
            aria-hidden="true"
          />
        </>
      )}
    </span>
  )
}
