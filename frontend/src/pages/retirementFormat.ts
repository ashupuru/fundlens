/** Shared formatting + label maps for the Retirement pages. Kept out of the
 * component files so those export only components (React Fast Refresh
 * requirement, flagged by oxlint's react(only-export-components)).
 */

export const BRAND_COLOR = "#1c2b4a";
export const ACCENT_COLOR = "#2f8f83";
export const NEGATIVE_COLOR = "#b3402c";

export const REASON_MESSAGES: Record<string, string> = {
  retirement_profile_incomplete:
    "Fill in the retirement plan below to see a projection — nothing is assumed on your behalf.",
  current_age_at_or_past_retirement_age:
    "Your current age is at or past your retirement age. Post-retirement-only projections aren't supported yet.",
  life_expectancy_not_after_retirement_age:
    "Life expectancy must be later than retirement age.",
};

export const EXCLUDED_REASON_MESSAGES: Record<string, string> = {
  target_date_already_passed: "target date has passed",
  beyond_life_expectancy: "falls beyond life expectancy",
};

export function formatPaise(paise: number): string {
  const rupees = paise / 100;
  if (Math.abs(rupees) >= 10_000_000) return `₹${(rupees / 10_000_000).toFixed(2)} Cr`;
  if (Math.abs(rupees) >= 100_000) return `₹${(rupees / 100_000).toFixed(2)} L`;
  return `₹${rupees.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}
