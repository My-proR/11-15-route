import { Notice } from "./ui";
import type { FeatureState } from "../lib/types";

const why: Record<string, string> = {
  deployment: "This feature is switched off for the whole deployment (server environment flag) to keep hosting cost and data collection down.",
  platform: "The platform administrator has not enabled this feature.",
  organization: "Your organization administrator has not enabled this feature.",
};
export function FeatureNotice({ name, state }: { name: string; state?: FeatureState }) {
  if (!state || state.effective) return null;
  return <Notice tone="info"><strong>{name} is off.</strong> {state.blocked_by ? why[state.blocked_by] : ""}</Notice>;
}
