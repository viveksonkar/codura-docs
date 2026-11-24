/** widget.js entry: define <codura-chat>, expose window.CoduraChat, auto-mount from the script tag. */
import { autoMount, define } from "./element";

const script = document.currentScript as HTMLScriptElement | null;
define();
autoMount(script);

export { CoduraChat, CoduraChatElement, define } from "./element";
