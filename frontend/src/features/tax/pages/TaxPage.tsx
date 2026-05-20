import * as React from "react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/primitives/Tabs";
import { AllowanceGauge } from "../components/AllowanceGauge";
import { S104PoolTable } from "../components/S104PoolTable";
import { OpenPositionsPanel } from "../components/OpenPositionsPanel";
import { TaxYearSelector } from "../components/TaxYearSelector";

function currentTaxYear(): number {
  const d = new Date();
  if (d.getMonth() > 3 || (d.getMonth() === 3 && d.getDate() >= 6)) {
    return d.getFullYear();
  }
  return d.getFullYear() - 1;
}

export function TaxPage(): React.JSX.Element {
  const [taxYear, setTaxYear] = React.useState(currentTaxYear);

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Tax</h1>
        <TaxYearSelector value={taxYear} onChange={setTaxYear} />
      </div>

      <AllowanceGauge taxYear={taxYear} />
      <OpenPositionsPanel />

      <Tabs defaultValue="pool">
        <TabsList>
          <TabsTrigger value="pool">S104 Pool</TabsTrigger>
          <TabsTrigger value="disposals" disabled>
            Disposals (23b)
          </TabsTrigger>
          <TabsTrigger value="income" disabled>
            Income (23b)
          </TabsTrigger>
          <TabsTrigger value="shorts" disabled>
            Shorts (23b)
          </TabsTrigger>
          <TabsTrigger value="futures" disabled>
            Futures (23b)
          </TabsTrigger>
        </TabsList>
        <TabsContent value="pool">
          <S104PoolTable />
        </TabsContent>
      </Tabs>
    </div>
  );
}
