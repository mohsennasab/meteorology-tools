// Storm centers computed with HEC-HMS's own code.
//
// Reproduces HEC-HMS 4.13 "Compute Storm Center" (hms.model.met.e.K):
//   1. Total precipitation over the full gridset record, read with the Vortex
//      TemporalDataReader that HEC-HMS uses (hms.model.data.d.b).
//   2. The first cell with the largest total (loop from Float.MIN_VALUE with a
//      strict ">"), so ties go to the first cell in the reader's order.
//   3. The cell center from the HEC-HMS grid definition class hms.b.f.g:
//      originX + (col + 0.5) dx, originY + (row + 0.5) dy.
//
// generate_hms_grid.py compiles and runs this file. To run it directly:
//   javac --release 17 -cp "<HMS>\hms.jar;<HMS>\lib\*" -d <classes> HmsStormCenter.java
//   <HMS>\jre\bin\java -Djava.library.path=<HMS>\bin -Dout=<results.csv>
//       -cp "<classes>;<HMS>\hms.jar;<HMS>\lib\*"
//       HmsStormCenter "/SHG1K/BASIN/PRECIPITATION/*/*/AORC/" <dss file | folder | --list=file> ...
//
// Output CSV (to -Dout, or stdout): dss,hms_x,hms_y,hms_max_total,units,n_cells_at_max
// The native DSS library also writes log lines to stdout, so use -Dout.

import java.io.File;
import java.io.FileOutputStream;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Optional;
import mil.army.usace.hec.vortex.VortexGrid;
import mil.army.usace.hec.vortex.geo.Grid;
import mil.army.usace.hec.vortex.io.DataReader;
import mil.army.usace.hec.vortex.io.TemporalDataReader;
import org.locationtech.jts.geom.Coordinate;

public class HmsStormCenter {
    public static void main(String[] args) throws Exception {
        if (args.length < 2) {
            System.err.println("Usage: HmsStormCenter <pathname> <dss file | folder | --list=file> ...");
            System.exit(2);
        }
        String pathname = args[0];
        List<String> files = new ArrayList<>();
        for (int k = 1; k < args.length; k++) {
            if (args[k].startsWith("--list=")) {
                for (String line : Files.readAllLines(Paths.get(args[k].substring(7)), StandardCharsets.UTF_8)) {
                    if (!line.isBlank()) {
                        files.add(line.strip());
                    }
                }
                continue;
            }
            File f = new File(args[k]);
            if (f.isDirectory()) {
                File[] found = f.listFiles((d, n) -> n.toLowerCase().endsWith(".dss"));
                Arrays.sort(found);
                for (File x : found) {
                    files.add(x.getPath());
                }
            } else {
                files.add(f.getPath());
            }
        }

        String outPath = System.getProperty("out");
        PrintStream out = outPath == null ? System.out
                : new PrintStream(new FileOutputStream(outPath), true, "UTF-8");
        out.println("dss,hms_x,hms_y,hms_max_total,units,n_cells_at_max");

        // The HMS grid definition class is obfuscated (hms.b.f.g). Load it by name
        // because javac resolves "hms.b.f.g" to a member of class hms.b.f.
        Class<?> hmsGrid = Class.forName("hms.b.f.g");

        for (String dss : files) {
            DataReader reader = DataReader.builder().path(dss).variable(pathname).build();
            try (TemporalDataReader temporal = TemporalDataReader.create(reader)) {
                Optional<VortexGrid> total = temporal.read(
                        temporal.getStartTime().orElseThrow(), temporal.getEndTime().orElseThrow());
                float[] values = total.orElseThrow().data();

                int index = 0;
                float max = Float.MIN_VALUE;
                for (int i = 0; i < values.length; i++) {
                    if (values[i] > max) {
                        max = values[i];
                        index = i;
                    }
                }
                int ties = 0;
                for (float v : values) {
                    if (v == max) {
                        ties++;
                    }
                }

                Grid definition = temporal.getGridDefinition().orElseThrow();
                Object hmsDefinition = hmsGrid.getMethod("a", Grid.class).invoke(null, definition);
                Coordinate center = (Coordinate) hmsGrid.getMethod("b", int.class).invoke(hmsDefinition, index);
                out.printf("%s,%.1f,%.1f,%.4f,%s,%d%n", dss, center.getX(), center.getY(), max,
                        total.get().units(), ties);
            }
        }
        out.close();
    }
}
