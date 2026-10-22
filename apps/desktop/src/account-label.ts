import type { Translate } from "./i18n";

/** A broker account as the user knows it, without any account number. */
export function accountLabel(ref: string, t: Translate): string {
  if (ref.startsWith("toss-")) {
    return t("journal.accountToss", { n: ref.split("-").at(-1) ?? "?" });
  }
  if (ref.startsWith("kiwoom-")) return t("journal.accountKiwoom");
  return ref;
}
