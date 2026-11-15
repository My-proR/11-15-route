import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";
import { registerServiceWorker } from "./lib/pwa";
import { useLang } from "./lib/i18n";

document.documentElement.lang = useLang.getState().lang;
registerServiceWorker();

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
