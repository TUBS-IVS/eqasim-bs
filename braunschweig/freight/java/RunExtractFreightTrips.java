package org.eqasim.braunschweig.freight;

import org.matsim.application.prepare.longDistanceFreightGER.tripExtraction.ExtractRelevantFreightTrips;
import picocli.CommandLine;

/**
 * Entry point for the freight trip extraction: a passthrough to MATSim's stock
 * {@link ExtractRelevantFreightTrips} command so the routing depends on the pinned MATSim
 * version and the inputs only, never on the eqasim fork jar (spec 2026-10-16, section 4.8).
 */
public class RunExtractFreightTrips {
	public static void main(String[] args) {
		System.exit(new CommandLine(new ExtractRelevantFreightTrips()).execute(args));
	}
}
