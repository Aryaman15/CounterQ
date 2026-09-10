"use client";

import { Check, RotateCw } from "lucide-react";
import { useState } from "react";

import type {
  CandidateProfileUpdate,
  CounterQApiClient,
  CurrentUserResponse,
} from "@/lib/counterq-api";

import { AuthenticatedProductPage } from "./AuthenticatedProductPage";

export function AccountPageExperience() {
  return (
    <AuthenticatedProductPage>
      {({ api, me }) => <AccountSettings api={api} me={me} />}
    </AuthenticatedProductPage>
  );
}

function AccountSettings({ api, me }: { api: CounterQApiClient; me: CurrentUserResponse }) {
  const profile = me.profile!;
  const [form, setForm] = useState<CandidateProfileUpdate>({
    display_name: profile.display_name,
    preferred_language: profile.preferred_language,
    default_interview_mode: profile.default_interview_mode,
    interview_level: profile.interview_level,
    target_role: profile.target_role,
    timezone: profile.timezone,
  });
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [failed, setFailed] = useState(false);

  async function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setSaved(false);
    setFailed(false);
    try {
      await api.saveProfile(form);
      setSaved(true);
    } catch {
      setFailed(true);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="account-page">
      <header className="product-page-heading">
        <p className="product-kicker">Account</p>
        <h1>Your interview defaults, kept separate from your evidence.</h1>
        <p>These preferences shape future setup. Existing interviews keep the configuration they started with.</p>
      </header>

      <div className="account-layout">
        <form className="account-form" onSubmit={save} aria-busy={saving}>
          <fieldset disabled={saving}>
            <legend>Candidate profile</legend>
            <label>
              Display name <span>Optional</span>
              <input
                maxLength={120}
                value={form.display_name ?? ""}
                onChange={(event) => {
                  setSaved(false);
                  setForm({ ...form, display_name: event.target.value || null });
                }}
              />
            </label>
            <label>
              Target role <span>Optional</span>
              <input
                maxLength={160}
                placeholder="Backend engineer"
                value={form.target_role ?? ""}
                onChange={(event) => {
                  setSaved(false);
                  setForm({ ...form, target_role: event.target.value || null });
                }}
              />
            </label>
            <label>
              Interview level
              <select
                value={form.interview_level}
                onChange={(event) => {
                  setSaved(false);
                  setForm({ ...form, interview_level: event.target.value as CandidateProfileUpdate["interview_level"] });
                }}
              >
                <option value="INTERN">Intern</option>
                <option value="NEW_GRAD">New graduate</option>
                <option value="EARLY_CAREER">Early career</option>
              </select>
            </label>
            <label>
              Preferred coding language
              <select
                value={form.preferred_language}
                onChange={(event) => {
                  setSaved(false);
                  setForm({ ...form, preferred_language: event.target.value as CandidateProfileUpdate["preferred_language"] });
                }}
              >
                <option value="cpp">C++</option>
                <option value="java">Java</option>
                <option value="python">Python</option>
              </select>
            </label>
            <label>
              Default interview mode
              <select
                value={form.default_interview_mode}
                onChange={(event) => {
                  setSaved(false);
                  setForm({ ...form, default_interview_mode: event.target.value as CandidateProfileUpdate["default_interview_mode"] });
                }}
              >
                <option value="SIMULATION">Simulation</option>
                <option value="COACH">Coach</option>
              </select>
            </label>
            <label>
              Timezone <span>Optional</span>
              <input
                maxLength={64}
                placeholder="Asia/Kolkata"
                value={form.timezone ?? ""}
                onChange={(event) => {
                  setSaved(false);
                  setForm({ ...form, timezone: event.target.value || null });
                }}
              />
            </label>
          </fieldset>
          {failed ? (
            <p className="account-form-error" role="alert"><RotateCw size={14} /> Your changes were not saved. Try again.</p>
          ) : null}
          {saved ? (
            <p className="account-form-saved" role="status"><Check size={14} /> Preferences saved.</p>
          ) : null}
          <button className="account-save" type="submit" disabled={saving}>
            {saving ? "Saving preferences…" : "Save preferences"}
          </button>
        </form>

        <aside className="account-privacy" aria-labelledby="privacy-title">
          <p className="home-section-label">Privacy and data</p>
          <h2 id="privacy-title">Only retain what the learning record needs.</h2>
          <dl>
            <div><dt>Raw interview audio</dt><dd>Not retained</dd></div>
            <div><dt>Camera and screen recording</dt><dd>Not collected</dd></div>
            <div><dt>Learning record</dt><dd>Finalized transcript and meaningful code snapshots may be retained with their provenance.</dd></div>
          </dl>
          <p>Reports, CounterMaps, and Mastery are derived from validated evidence and can be rebuilt from canonical records.</p>
        </aside>
      </div>
    </div>
  );
}
