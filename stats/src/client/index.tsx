import "./styles.css";
import { createRoot } from "react-dom/client";
import App from "./App";
// After App, so the bundle places it after every route's own CSS: the phone layer overrides them all.
import "./phone.css";

const root = document.getElementById("root");
if (!root) throw new Error("missing #root");
createRoot(root).render(<App />);
