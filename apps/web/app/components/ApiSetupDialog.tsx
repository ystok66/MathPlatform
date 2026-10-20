"use client";

import { useEffect, useRef, useState } from "react";
import { KeyRound, X } from "lucide-react";
import { languageFetch, setLanguage, LANGUAGES } from "../i18n/language";
import { useI18n } from "../i18n/provider";
import styles from "./ApiSetupDialog.module.css";

type Settings = {
  provider: string; base_url: string; api_key?: string | null; has_api_key: boolean;
  default_model: string; roles: Record<string, string>; star_generator_models: Record<string, string>;
  model_price_json: string; strict_reviewer_model_allowlist: string;
};
type Discovery = { models: string[]; error?: string | null };
type TestResult = { success: boolean; chat_ok: boolean; elapsed_ms: number; error?: string | null };
type Phase = "models" | "test" | "save" | null;
const ROLES = ["planner", "generator", "reviewer", "secondary_reviewer", "reporter", "diagnostician", "hint_generator", "hint_reviewer", "fault_auditor", "set_reviewer", "answer_judge", "solution_explainer", "question_repair_reviewer", "emergency_recovery_agent", "project_assistant"];

export function ApiSetupDialog({ settings, ready, enabled, openRequest, apiBase, onSaved }: {
  settings: Settings; ready: boolean; enabled: boolean; openRequest: number; apiBase: string;
  onSaved: (settings: Settings) => void;
}) {
  const { language, t } = useI18n();
  const dialog = useRef<HTMLDialogElement>(null);
  const dismissed = useRef(false);
  const handledRequest = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const [opened, setOpened] = useState(false);
  const [baseUrl, setBaseUrl] = useState("");
  const [key, setKey] = useState("");
  const [model, setModel] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const [allRoles, setAllRoles] = useState(true);
  const [phase, setPhase] = useState<Phase>(null);
  const [error, setError] = useState("");
  const [tested, setTested] = useState<number | null>(null);

  useEffect(() => {
    if (!ready || !enabled || opened) return;
    const requested = openRequest > handledRequest.current;
    if (!requested && (settings.has_api_key || dismissed.current)) return;
    handledRequest.current = openRequest;
    setBaseUrl(settings.base_url || "");
    setKey(""); setModel(settings.default_model || ""); setModels([]); setSearch("");
    setAllRoles(!settings.has_api_key); setError(""); setTested(null); setOpened(true);
  }, [ready, enabled, opened, openRequest, settings]);

  useEffect(() => {
    if (opened && dialog.current && !dialog.current.open) dialog.current.showModal();
    if (!opened && dialog.current?.open) dialog.current.close();
  }, [opened]);
  useEffect(() => () => { controller.current?.abort(); }, []);

  function close() {
    if (phase === "save") return;
    dismissed.current = true;
    controller.current?.abort(); controller.current = null;
    setPhase(null); setKey(""); setError(""); setTested(null); setOpened(false);
  }
  function invalidate() { setTested(null); setError(""); }
  function endpoint() {
    let url: URL;
    try { url = new URL(baseUrl.trim()); } catch { throw new Error(t("admin.enter_a_complete_api_base_url_including_https")); }
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
      throw new Error(t("common.use_an_http_s_base_url_without_credentials_query"));
    }
    if (/\/(models|chat\/completions|responses)\/?$/.test(url.pathname)) {
      throw new Error(t("common.enter_the_base_url_usually_ending_in_v1_not"));
    }
    if (!key.trim() && !settings.has_api_key) throw new Error(t("admin.enter_your_api_key_first"));
    if (!key.trim() && url.href.replace(/\/$/, "") !== settings.base_url.trim().replace(/\/$/, "")) {
      throw new Error(t("common.enter_a_key_for_the_new_url_the_saved"));
    }
    return url.href.replace(/\/$/, "");
  }
  function payload() {
    return { ...settings, provider: "openai_compatible", base_url: endpoint(), api_key: key.trim() || null, default_model: model };
  }
  async function run<T>(kind: Exclude<Phase, null>, path: string, body: object, done: (result: T) => void) {
    if (controller.current) return;
    const current = new AbortController(); controller.current = current;
    setPhase(kind); setError("");
    const timeout = window.setTimeout(() => current.abort(), kind === "test" ? 75000 : 20000);
    try {
      const response = await languageFetch(`${apiBase}/api/settings/models${path}`, {
        method: kind === "save" ? "PUT" : "POST", signal: current.signal,
        headers: { "Content-Type": "application/json", "X-Math-Platform-Client": "math-platform-web" },
        body: JSON.stringify(body),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.user_message || (typeof result.detail === "string" ? result.detail : `HTTP ${response.status}`));
      if (controller.current === current) done(result as T);
    } catch (cause) {
      if (controller.current !== current) return;
      let message = cause instanceof Error ? cause.message : String(cause);
      if (current.signal.aborted) message = kind === "save"
        ? t("admin.saving_timed_out_recheck_the_local_service_and_reopen")
        : t("common.the_request_timed_out_check_the_url_and_local");
      if (key.trim()) message = message.split(key.trim()).join("[redacted]");
      setError(message.slice(0, 600));
    } finally {
      window.clearTimeout(timeout);
      if (controller.current === current) { controller.current = null; setPhase(null); }
    }
  }
  function discover() {
    try {
      const body = payload(); setTested(null); setModels([]);
      void run<Discovery>("models", "/discover", body, result => {
        if (result.error) throw new Error(result.error);
        const found = [...new Set(result.models.filter(value => typeof value === "string" && value.trim()))];
        if (!found.length) throw new Error(t("admin.this_provider_returned_no_models_check_its_url_key"));
        setModels(found); setSearch("");
        setModel(found.includes(model) ? model : found[0]);
      });
    } catch (cause) { setError((cause as Error).message); }
  }
  function test() {
    try {
      const body = payload(); setTested(null);
      if (!model) throw new Error(t("admin.select_a_model_first"));
      void run<TestResult>("test", "/test", body, result => {
        if (!result.success || !result.chat_ok) throw new Error(result.error || t("admin.the_model_did_not_return_a_valid_reply"));
        setTested(result.elapsed_ms);
      });
    } catch (cause) { setError((cause as Error).message); }
  }
  function save() {
    if (tested == null) return;
    try {
      const body = payload();
      if (allRoles) {
        body.roles = Object.fromEntries(ROLES.map(role => [role, model]));
        body.star_generator_models = { le_3: model, star_4: model, star_5: model };
        body.strict_reviewer_model_allowlist = `openai_compatible:${model}`;
        body.model_price_json = "";
      }
      void run<Settings>("save", "", body, result => {
        onSaved(result); dismissed.current = true; setKey(""); setOpened(false);
      });
    } catch (cause) { setError((cause as Error).message); }
  }
  function cancel() {
    controller.current?.abort(); controller.current = null; setPhase(null); setTested(null);
    setError(t("admin.stopped_waiting_a_request_already_sent_to_the_provider"));
  }
  const visibleModels = models.filter(value => value.toLowerCase().includes(search.toLowerCase()));
  return (
    <dialog ref={dialog} className={styles.dialog} aria-labelledby="api-setup-title" onCancel={event => { event.preventDefault(); close(); }}>
      {opened && <div className={styles.body}>
        <div className={styles.top}>
          <span className={styles.badge}><KeyRound size={15} />{t("admin.local_app_your_provider")}</span>
          <button type="button" className={styles.close} onClick={close} disabled={phase === "save"} aria-label={t("admin.close_api_setup")}><X size={20} /></button>
        </div>
        <div className={styles.languages} aria-label="Language">
          {LANGUAGES.map(item => <button key={item.code} type="button" aria-pressed={language === item.code} onClick={() => setLanguage(item.code)}>{item.native_name}</button>)}
        </div>
        <h2 id="api-setup-title">{t("common.connect_your_ai")}</h2>
        <p>{t("practice.set_up_an_openai_compatible_chat_completions_provider_you")}</p>
        <ol className={styles.steps} aria-label={t("common.setup_progress")}>
          <li data-done={models.length > 0}>1. {t("common.connect")}</li>
          <li data-done={tested != null}>2. {t("practice.choose_test")}</li>
          <li>3. {t("common.save")}</li>
        </ol>
        <fieldset className={styles.fields} disabled={phase != null}>
          <label htmlFor="api-setup-url">{t("admin.api_base_url")}</label>
          <input id="api-setup-url" type="url" autoFocus spellCheck={false} value={baseUrl} placeholder="https://your-provider.example/v1" onChange={event => { setBaseUrl(event.target.value); setModels([]); invalidate(); }} />
          <small>{t("admin.include_v1_if_required_by_your_provider_do_not")}</small>
          <label htmlFor="api-setup-key">API Key</label>
          <input id="api-setup-key" type="password" autoComplete="new-password" spellCheck={false} value={key} placeholder={settings.has_api_key ? t("common.saved_key_leave_blank_to_keep") : t("admin.paste_your_api_key")} onChange={event => { setKey(event.target.value); setModels([]); invalidate(); }} />
          <button type="button" className={styles.primary} onClick={discover}>{phase === "models" ? t("common.fetching_models") : t("common.connect_fetch_models")}</button>
          {models.length > 0 && <>
            <label htmlFor="api-setup-search">{t("admin.find_a_model")} ({models.length})</label>
            <input id="api-setup-search" value={search} onChange={event => setSearch(event.target.value)} placeholder={t("admin.filter_the_provider_s_model_list")} />
            <label htmlFor="api-setup-model">{t("admin.selected_model")}</label>
            <select id="api-setup-model" value={model} onChange={event => { setModel(event.target.value); invalidate(); }}>
              {!visibleModels.includes(model) && model && <option value={model}>{model}</option>}
              {visibleModels.map(value => <option key={value} value={value}>{value}</option>)}
            </select>
            {!visibleModels.length && <small>{t("admin.no_search_matches_your_selected_model_is_retained")}</small>}
            <small>{t("practice.listed_models_are_advertised_by_the_provider_not_all")}</small>
            <button type="button" onClick={test}>{phase === "test" ? t("admin.calling_the_selected_model") : t("practice.test_an_actual_model_call")}</button>
            <small>{t("generation.sends_one_short_chat_request_up_to_64_output")}</small>
          </>}
          <label className={styles.checkbox}><input type="checkbox" checked={allRoles} onChange={event => setAllRoles(event.target.checked)} />{t("admin.use_this_model_for_all_ai_roles_and_star")}</label>
          <small>{allRoles
            ? t("generation.replaces_role_star_level_reviewer_allowlist_and_price_settings")
            : t("common.existing_role_and_star_level_choices_are_retained_they")}</small>
        </fieldset>
        <div aria-live="polite" className={styles.feedback}>
          {tested != null && <p className={styles.success}>{t("common.actual_call_succeeded")} / {model} / {tested} ms</p>}
          {error && <p role="alert" className={styles.error}>{error}</p>}
        </div>
        <div className={styles.footer}>
          <button type="button" onClick={close} disabled={phase === "save"}>{t("common.not_now")}</button>
          {phase && phase !== "save" && <button type="button" onClick={cancel}>{t("common.cancel_request")}</button>}
          <button type="button" className={styles.primary} disabled={tested == null || phase != null} onClick={save}>{phase === "save" ? t("common.saving_91a18d71") : t("common.save_start")}</button>
        </div>
        <small>{t("admin.your_key_is_saved_by_this_local_app_only")}</small>
      </div>}
    </dialog>
  );
}
