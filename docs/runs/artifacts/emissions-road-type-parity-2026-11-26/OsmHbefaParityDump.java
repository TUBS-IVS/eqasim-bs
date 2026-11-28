import java.io.BufferedWriter;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.TreeSet;

import org.matsim.api.core.v01.network.Link;
import org.matsim.api.core.v01.network.Network;
import org.matsim.contrib.emissions.OsmHbefaMapping;
import org.matsim.core.network.NetworkUtils;

/**
 * Writes, for every link of a MATSim network, the HBEFA road type that the emissions contrib's own
 * {@code OsmHbefaMapping} assigns. Used to prove the Python port in
 * {@code braunschweig/emissions/road_types.py} equal to the original (ADR-0209, amendment A7).
 *
 * <p>Run as a single-file source program against the jar the pipeline already builds; nothing is
 * compiled into a jar and no class of the pipeline is changed:
 * {@code java -cp <pipeline jar> OsmHbefaParityDump.java <network.xml.gz> <output.tsv>}
 *
 * <p>The contrib reads the OSM class from the link attribute {@code type}. The Braunschweig network
 * carries it in {@code osm:way:highway}, so that value is copied into {@code type} in memory first
 * (the same step eqasim's {@code RunComputeEmissionsEvents} performs). Nothing else is altered: no
 * {@code _link} rewriting, no default for unknown classes.
 *
 * <p>The mapping is called once per link instead of through {@code addHbefaMappings(network)},
 * because the original throws on the first class it does not know and would then say nothing about
 * the other links. {@code addHbefaMappings} does nothing but call the same method for every link.
 * A thrown exception is written to the {@code java_error} column.
 */
public class OsmHbefaParityDump {

    private static final String OSM_HIGHWAY_ATTRIBUTE = "osm:way:highway";
    private static final String ALLOWED_SPEED_ATTRIBUTE = "allowed_speed";

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            System.err.println("usage: java -cp <pipeline jar> OsmHbefaParityDump.java <network.xml.gz> <output.tsv>");
            System.exit(2);
        }
        Path outputPath = Path.of(args[1]);
        if (Files.exists(outputPath)) {
            System.err.println("refusing to overwrite " + outputPath);
            System.exit(2);
        }

        System.err.println("java.version=" + System.getProperty("java.version"));
        System.err.println("OsmHbefaMapping loaded from "
                + OsmHbefaMapping.class.getProtectionDomain().getCodeSource().getLocation());

        Network network = NetworkUtils.readNetwork(args[0]);
        OsmHbefaMapping mapping = OsmHbefaMapping.build();
        // Looked up by reflection so the program does not depend on the method's visibility in the pinned version.
        Method determineHbefaType = OsmHbefaMapping.class.getDeclaredMethod("determineHbefaType", Link.class);
        determineHbefaType.setAccessible(true);

        List<Link> links = new ArrayList<>(network.getLinks().values());
        links.sort(Comparator.comparing(link -> link.getId().toString()));

        long mapped = 0;
        long failed = 0;
        try (BufferedWriter writer = Files.newBufferedWriter(outputPath, StandardCharsets.UTF_8)) {
            writer.write("link_id\tmodes\tosm_highway\ttype_before\tfreespeed_meters_per_second\tallowed_speed_attribute"
                    + "\tjava_road_type\tjava_error\n");
            for (Link link : links) {
                Object osmHighway = link.getAttributes().getAttribute(OSM_HIGHWAY_ATTRIBUTE);
                Object allowedSpeed = link.getAttributes().getAttribute(ALLOWED_SPEED_ATTRIBUTE);
                String typeBefore = NetworkUtils.getType(link);
                if (osmHighway != null) {
                    NetworkUtils.setType(link, osmHighway.toString());
                }

                String roadType = "";
                String error = "";
                try {
                    roadType = (String) determineHbefaType.invoke(mapping, link);
                    mapped++;
                } catch (InvocationTargetException exception) {
                    Throwable cause = exception.getCause();
                    error = cause.getClass().getName() + ": " + cause.getMessage();
                    failed++;
                }

                writer.write(String.join("\t",
                        clean(link.getId().toString()),
                        clean(String.join(",", new TreeSet<>(link.getAllowedModes()))),
                        clean(osmHighway),
                        clean(typeBefore),
                        Double.toString(link.getFreespeed()),
                        clean(allowedSpeed),
                        clean(roadType),
                        clean(error)));
                writer.write("\n");
            }
        }
        System.err.println("links=" + links.size() + " mapped=" + mapped + " exception=" + failed);
    }

    /** Empty text for a missing value; tabs and line breaks would break the table. */
    private static String clean(Object value) {
        if (value == null) {
            return "";
        }
        return value.toString().replace('\t', ' ').replace('\n', ' ').replace('\r', ' ');
    }
}
