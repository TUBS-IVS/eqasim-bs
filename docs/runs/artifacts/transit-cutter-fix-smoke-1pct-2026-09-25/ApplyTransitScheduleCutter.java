import java.io.File;
import java.util.Optional;

import org.eqasim.core.scenario.cutter.extent.ScenarioExtent;
import org.eqasim.core.scenario.cutter.extent.ShapeScenarioExtent;
import org.eqasim.core.scenario.cutter.transit.DefaultStopSequenceCrossingPointFinder;
import org.eqasim.core.scenario.cutter.transit.TransitScheduleCutter;
import org.matsim.api.core.v01.Scenario;
import org.matsim.core.config.ConfigUtils;
import org.matsim.core.scenario.ScenarioUtils;
import org.matsim.pt.transitSchedule.api.TransitScheduleReader;
import org.matsim.pt.transitSchedule.api.TransitScheduleWriter;

/**
 * Applies eqasim TransitScheduleCutter (from the JAR on the classpath) to a transit schedule with a cordon extent
 * and writes the cut schedule, so a cutter build can be checked on a real schedule without the pipeline.
 * Usage: java -cp <braunschweig JAR>:. ApplyTransitScheduleCutter <extent.gpkg> <schedule.xml.gz> <cut schedule.xml.gz>
 */
public class ApplyTransitScheduleCutter {
	public static void main(String[] args) throws Exception {
		ScenarioExtent extent = new ShapeScenarioExtent.Builder(new File(args[0]), Optional.empty(), Optional.empty()).build();
		Scenario scenario = ScenarioUtils.createScenario(ConfigUtils.createConfig());
		new TransitScheduleReader(scenario).readFile(args[1]);
		new TransitScheduleCutter(extent, new DefaultStopSequenceCrossingPointFinder(extent)).run(scenario.getTransitSchedule());
		new TransitScheduleWriter(scenario.getTransitSchedule()).writeFile(args[2]);
	}
}
