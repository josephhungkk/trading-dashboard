import { createFileRoute } from "@tanstack/react-router"

import { EarningsPage } from "../features/earnings/EarningsPage"

export const Route = createFileRoute("/earnings")({
  component: EarningsPage,
})
