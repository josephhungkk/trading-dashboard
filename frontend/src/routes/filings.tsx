import { createFileRoute } from "@tanstack/react-router"
import { FilingsPage } from "../features/filings/FilingsPage"

export const Route = createFileRoute("/filings")({
  component: FilingsPage,
})
