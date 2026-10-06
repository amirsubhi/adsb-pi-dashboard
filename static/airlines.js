// ICAO airline designators, for turning a callsign's 3-letter prefix into a
// readable airline name (e.g. "AXM220" -> "AirAsia"). Not exhaustive: an
// unrecognised prefix just leaves the bare callsign shown, as before this
// file existed. Covers the carriers this project's own feeders see most
// (Malaysia and the surrounding region) plus other widely-flown airlines.
"use strict";
const AIRLINES = {
  MAS: "Malaysia Airlines", AXM: "AirAsia", XAX: "AirAsia X", AWQ: "Indonesia AirAsia",
  MXD: "Batik Air Malaysia", FFM: "Firefly", BVT: "Berjaya Air", RMF: "Royal Malaysian Air Force",
  SIA: "Singapore Airlines", TGW: "Scoot", RBA: "Royal Brunei Airlines",
  THA: "Thai Airways International", NOK: "Nok Air", BKP: "Bangkok Airways",
  GIA: "Garuda Indonesia", LNI: "Lion Air", SJY: "Sriwijaya Air", TNU: "TransNusa",
  HVN: "Vietnam Airlines", VJC: "VietJet Air", PAL: "Philippine Airlines",
  CPA: "Cathay Pacific", HKE: "Hong Kong Express Airways",
  CSN: "China Southern Airlines", CES: "China Eastern Airlines", CCA: "Air China",
  CXA: "Xiamen Airlines", CSC: "Sichuan Airlines", CSH: "Shanghai Airlines",
  CSZ: "Shenzhen Airlines", CHH: "Hainan Airlines", CQH: "Spring Airlines",
  DKH: "Juneyao Airlines", QDA: "Qingdao Airlines", CAL: "China Airlines",
  JAL: "Japan Airlines", ANA: "All Nippon Airways", KAL: "Korean Air", AAR: "Asiana Airlines",
  EVA: "EVA Air", IGO: "IndiGo", AIC: "Air India", ALK: "SriLankan Airlines",
  UBG: "US-Bangla Airlines", HIM: "Himalaya Airlines",
  SVA: "Saudi Arabian Airlines", ETD: "Etihad Airways", UAE: "Emirates", QTR: "Qatar Airways",
  BAW: "British Airways", AFR: "Air France", DLH: "Lufthansa", KLM: "KLM Royal Dutch Airlines",
  SWR: "Swiss International Air Lines", AUA: "Austrian Airlines", FIN: "Finnair",
  EIN: "Aer Lingus", VIR: "Virgin Atlantic", RYR: "Ryanair", EZY: "easyJet",
  VJT: "VistaJet",
  UAL: "United Airlines", AAL: "American Airlines", DAL: "Delta Air Lines",
  SWA: "Southwest Airlines", JBU: "JetBlue Airways", ACA: "Air Canada", WJA: "WestJet",
};

// Matches an ICAO callsign (3 letters, then a flight number) and looks up
// its airline; returns null for a bare registration or an unknown prefix.
function airlineOf(flight){
  if (!flight) return null;
  const m = /^([A-Z]{3})\d/.exec(flight.trim());
  return (m && AIRLINES[m[1]]) || null;
}
