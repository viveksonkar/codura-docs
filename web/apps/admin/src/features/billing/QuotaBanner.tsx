/** Console banner for 80 % / 100 % quota notices and past-due status (FR-64, billing.md §6–7). */
import { useEffect, useState } from "react";
import { get } from "../../api";
import { Link } from "../../router";
import { METER_LABEL } from "./money";

interface Usage { status: string; overage_enabled: boolean; notices: { meter: string; threshold: number }[] }

export function QuotaBanner({ path }: { path: string }) {
  const [usage, setUsage] = useState<Usage | null>(null);
  useEffect(() => {
    get<Usage>("/api/admin/usage").then(setUsage).catch(() => setUsage(null));
  }, [path]);
  if (!usage || path === "/billing") return null;
  if (usage.status === "past_due") {
    return <div className="alert alert-error">Your subscription is past due: chat is paused and changes are disabled. <Link to="/billing">Go to Billing</Link></div>;
  }
  const top = usage.notices.find((n) => n.threshold >= 100) ?? usage.notices[0];
  if (!top) return null;
  const label = (METER_LABEL[top.meter] ?? top.meter).toLowerCase();
  const text = top.threshold >= 100
    ? usage.overage_enabled
      ? `You have used all included ${label} this month; extra usage is billed as overage.`
      : `You have used all included ${label} this month; new requests are paused.`
    : `You have used ${top.threshold}% of your ${label} this month.`;
  return <div className={`alert ${top.threshold >= 100 ? "alert-error" : "alert-info"}`}>{text} <Link to="/billing">Upgrade or view usage</Link></div>;
}
