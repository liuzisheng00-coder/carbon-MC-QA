import React from "react";
import { createRoot } from "react-dom/client";
import DM2CApp from "../DM2CApp.connected.jsx";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <DM2CApp />
  </React.StrictMode>
);
