import { describe, expect, it } from "vitest";
import { compactNumber, duration, percent, relativeTime, repoName, shortSha, titleCase, usd } from "./format";

const NOW = Date.parse("2026-09-14T12:00:00.000Z");
const ago = (secs: number) => new Date(NOW - secs * 1000).toISOString();

describe("relativeTime", () => {
  it("returns '-' for missing or invalid input", () => {
    expect(relativeTime(null, NOW)).toBe("-");
    expect(relativeTime(undefined, NOW)).toBe("-");
    expect(relativeTime("not-a-date", NOW)).toBe("-");
  });

  it("says 'just now' for anything under 10 seconds", () => {
    expect(relativeTime(ago(0), NOW)).toBe("just now");
    expect(relativeTime(ago(9), NOW)).toBe("just now");
  });

  it("formats seconds, minutes, hours and days in the past", () => {
    expect(relativeTime(ago(30), NOW)).toMatch(/30 seconds ago/);
    expect(relativeTime(ago(5 * 60), NOW)).toMatch(/5 minutes ago/);
    expect(relativeTime(ago(3 * 3600), NOW)).toMatch(/3 hours ago/);
    expect(relativeTime(ago(2 * 86400), NOW)).toMatch(/2 days ago/);
  });

  it("uses natural phrasing for single units and future times", () => {
    expect(relativeTime(ago(86400), NOW)).toBe("yesterday");
    expect(relativeTime(ago(-2 * 3600), NOW)).toMatch(/in 2 hours/);
  });
});

describe("usd", () => {
  it("returns '-' for null/NaN", () => {
    expect(usd(null)).toBe("-");
    expect(usd(undefined)).toBe("-");
    expect(usd(Number.NaN)).toBe("-");
  });

  it("uses 2 digits by default and 3 for sub-dollar amounts", () => {
    expect(usd(0)).toBe("$0.00");
    expect(usd(12.5)).toBe("$12.50");
    expect(usd(0.123456)).toBe("$0.123");
  });

  it("honours explicit digits and compact thousands", () => {
    expect(usd(5, { digits: 0 })).toBe("$5");
    expect(usd(1234, { compact: true })).toBe("$1.2k");
    expect(usd(999, { compact: true })).toBe("$999.00");
  });
});

describe("percent", () => {
  it("formats ratios as percentages", () => {
    expect(percent(0.5)).toBe("50%");
    expect(percent(0.8234, 1)).toBe("82.3%");
    expect(percent(1)).toBe("100%");
  });

  it("returns '-' for missing values", () => {
    expect(percent(null)).toBe("-");
    expect(percent(Number.NaN)).toBe("-");
  });
});

describe("duration and misc", () => {
  it("scales duration units", () => {
    expect(duration(0.25)).toBe("250ms");
    expect(duration(4.26)).toBe("4.3s");
    expect(duration(42)).toBe("42s");
    expect(duration(125)).toBe("2m 05s");
    expect(duration(3 * 3600 + 7 * 60)).toBe("3h 07m");
    expect(duration(null)).toBe("-");
  });

  it("formats repo names, shas, numbers and titles", () => {
    expect(repoName("https://github.com/acme/payments-api.git")).toBe("acme/payments-api");
    expect(repoName("/srv/repos/internal-billing")).toBe("repos/internal-billing");
    expect(shortSha("0123456789abcdef")).toBe("0123456");
    expect(shortSha(null)).toBe("-");
    expect(compactNumber(1_500)).toBe("1.5k");
    expect(compactNumber(2_400_000)).toBe("2.4M");
    expect(titleCase("off_by_one")).toBe("Off By One");
  });
});
