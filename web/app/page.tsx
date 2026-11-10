import Console from "@/components/Console";
import data from "@/data/console.json";
import type { ConsoleData } from "@/lib/types";

export default function Page() {
  return <Console data={data as unknown as ConsoleData} />;
}
